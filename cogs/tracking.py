import discord
from discord.ext import commands
import asyncio
import logging
from datetime import datetime, timezone
from mod_validator import validate_mods

logger = logging.getLogger("tracking")


class TrackingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._active_tasks = {}
        logger.info("[TrackingCog] Cog geladen.")

    @property
    def db(self):
        return self.bot.db

    @property
    def osu(self):
        return self.bot.osu

    async def run_tracking(self, guild_id: int, session_id: int, interval: int, end_after: float = None):
        """Main tracking loop. Polls alle geregistreerde spelers elke `interval` seconden."""
        logger.info(f"[Tracking] ▶️  Gestart voor guild {guild_id}, sessie {session_id}, interval={interval}s, end_after={end_after}")
        start = asyncio.get_event_loop().time()
        self._active_tasks[guild_id] = asyncio.current_task()

        try:
            poll_count = 0
            while True:
                settings = await self.db.get_guild_settings(guild_id)
                if not settings["tracking_active"]:
                    logger.info(f"[Tracking] ⏹️  tracking_active=False gedetecteerd voor guild {guild_id}, stoppen.")
                    break

                if end_after and (asyncio.get_event_loop().time() - start) >= end_after:
                    logger.info(f"[Tracking] ⏰ Tijdsframe verstreken voor guild {guild_id}")
                    await self.db.update_guild_settings(guild_id, tracking_active=False)
                    await self.db.end_tracking_session(session_id)
                    guild = self.bot.get_guild(guild_id)
                    if guild and settings["score_channel_id"]:
                        ch = guild.get_channel(settings["score_channel_id"])
                        if ch:
                            await ch.send("⏹️ **Tracking automatisch gestopt** — tijdsframe verstreken.")
                    break

                poll_count += 1
                logger.info(f"[Tracking] 🔄 Poll #{poll_count} gestart voor guild {guild_id}")
                await self._poll_all_players(guild_id, settings)
                logger.info(f"[Tracking] ✅ Poll #{poll_count} klaar. Wacht {interval}s...")
                await asyncio.sleep(interval)

        except asyncio.CancelledError:
            logger.warning(f"[Tracking] ❌ Task gecancelled voor guild {guild_id}")
        except Exception as e:
            logger.exception(f"[Tracking] 💥 Onverwachte fout voor guild {guild_id}: {e}")
        finally:
            self._active_tasks.pop(guild_id, None)
            logger.info(f"[Tracking] Task beëindigd voor guild {guild_id}")

    async def _poll_all_players(self, guild_id: int, settings: dict):
        players = await self.db.get_all_players()
        logger.info(f"[Tracking] {len(players)} spelers gevonden om te pollen")

        guild = self.bot.get_guild(guild_id)
        score_channel = None
        if guild and settings.get("score_channel_id"):
            score_channel = guild.get_channel(settings["score_channel_id"])

        pools = await self.db.get_all_pools(guild_id)
        pool_map_ids = set()
        pool_map_lookup = {}
        for pool in pools:
            maps = await self.db.get_pool_maps(pool["id"])
            for m in maps:
                pool_map_ids.add(m["beatmap_id"])
                pool_map_lookup[m["beatmap_id"]] = (pool, m)

        pool_channels_to_refresh = set()
        new_scores_total = 0

        for player in players:
            try:
                raw_scores = await self.osu.get_recent_scores(player["osu_id"], limit=50)
                logger.info(f"[Tracking] {player['osu_username']}: {len(raw_scores) if raw_scores else 0} recente scores opgehaald")
                if not raw_scores:
                    continue

                for raw in raw_scores:
                    parsed = self.osu.parse_score(raw, player["osu_id"], player["discord_id"])
                    if not parsed["osu_score_id"]:
                        continue
                    exists = await self.db.score_exists(parsed["osu_score_id"])
                    if exists:
                        continue

                    await self.db.save_score(parsed)
                    new_scores_total += 1
                    logger.info(f"[Tracking] 💾 Nieuwe score: {player['osu_username']} op beatmap {parsed['beatmap_id']} ({parsed['score']:,})")

                    bid = parsed["beatmap_id"]
                    if bid in pool_map_ids:
                        pool, m = pool_map_lookup[bid]
                        is_valid, reason = validate_mods(parsed["mods"], m["slot"])
                        if not is_valid:
                            logger.info(f"[Tracking] ⚠️  Score ongeldig ({reason}) voor {player['osu_username']} op {m['slot']}")
                            async with self.db.pool.acquire() as conn:
                                await conn.execute(
                                    "UPDATE scores SET is_valid=FALSE, invalid_reason=$1 WHERE osu_score_id=$2",
                                    reason, parsed["osu_score_id"]
                                )
                            parsed["is_valid"] = False
                            parsed["invalid_reason"] = reason
                        pool_channels_to_refresh.add((pool["channel_id"], pool["id"], pool["name"]))

                    if score_channel:
                        await self._post_score_notification(score_channel, parsed, player, raw)

            except Exception as e:
                logger.exception(f"[Tracking] Fout bij speler {player['osu_username']}: {e}")

        logger.info(f"[Tracking] Poll klaar — {new_scores_total} nieuwe score(s), {len(pool_channels_to_refresh)} pool(s) refreshen")

        for (ch_id, pool_id, pool_name) in pool_channels_to_refresh:
            if guild:
                ch = guild.get_channel(ch_id)
                if ch:
                    admin_cog = self.bot.get_cog("AdminCog")
                    if admin_cog:
                        logger.info(f"[Tracking] 🔃 Leaderboard refreshen voor pool '{pool_name}'")
                        await admin_cog._update_pool_leaderboard(ch, pool_id, pool_name)

    async def _post_score_notification(self, channel: discord.TextChannel, score: dict, player, raw: dict):
        rank_colors = {
            "SS": 0xFFD700, "X": 0xFFD700, "XH": 0xC0C0C0,
            "S": 0xFFD700, "SH": 0xC0C0C0,
            "A": 0x50FA7B, "B": 0x8BE9FD, "C": 0xFFB86C,
            "D": 0xFF5555, "F": 0x6272A4
        }
        rank = score.get("rank", "F")
        color = rank_colors.get(rank, 0x44475A)

        bm = raw.get("beatmap", {})
        bms = raw.get("beatmapset", {})
        title = bms.get("title", "Unknown")
        artist = bms.get("artist", "Unknown")
        version = bm.get("version", "Unknown")
        beatmap_url = f"https://osu.ppy.sh/b/{score['beatmap_id']}"

        mods = f" +{score['mods']}" if score['mods'] != 'NM' else ""
        passed = "✅" if score["is_pass"] else "❌"
        valid_warning = f"\n⚠️ **Telt niet mee:** {score.get('invalid_reason', 'Ongeldige mods')}" if not score.get("is_valid", True) else ""

        embed = discord.Embed(
            title=f"{passed} {player['osu_username']}{mods}",
            url=beatmap_url,
            description=f"**{artist} - {title} [{version}]**{valid_warning}",
            color=color if score.get("is_valid", True) else 0x6272A4
        )
        embed.add_field(name="Score", value=f"`{score['score']:,}`", inline=True)
        embed.add_field(name="Accuracy", value=f"`{score['accuracy']:.2f}%`", inline=True)
        embed.add_field(name="Combo", value=f"`{score['max_combo']}x`", inline=True)
        embed.add_field(name="Rank", value=f"`{rank}`", inline=True)
        embed.add_field(name="Misses", value=f"`{score['count_miss']}`", inline=True)
        if score.get("pp"):
            embed.add_field(name="PP", value=f"`{score['pp']:.0f}pp`", inline=True)

        cover = bms.get("covers", {}).get("cover@2x") or bms.get("covers", {}).get("cover")
        if cover:
            embed.set_thumbnail(url=cover)

        embed.set_footer(text=f"osu! LAN Tracker • {score['submitted_at'].strftime('%H:%M:%S')}")
        await channel.send(embed=embed)


async def setup(bot):
    await bot.add_cog(TrackingCog(bot))
