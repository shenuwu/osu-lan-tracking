import discord
from discord.ext import commands
import asyncio
import logging
import json
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

    # ── Discord log channel helper ─────────────────────────────────────

    async def _discord_log(self, guild_id: int, message: str, color: int = 0x6272A4):
        """Stuur een log embed naar het ingestelde log channel."""
        try:
            settings = await self.db.get_guild_settings(guild_id)
            log_ch_id = settings.get("log_channel_id")
            if not log_ch_id:
                return
            guild = self.bot.get_guild(guild_id)
            if not guild:
                return
            ch = guild.get_channel(log_ch_id)
            if not ch:
                return
            embed = discord.Embed(
                description=message,
                color=color,
                timestamp=datetime.now(timezone.utc)
            )
            await ch.send(embed=embed)
        except Exception as e:
            logger.warning(f"discord_log mislukt: {e}")

    async def _discord_log_raw_score(self, guild_id: int, username: str, raw: dict, parsed: dict, slot: str = None, is_valid: bool = True, reason: str = None):
        """Stuur een gedetailleerde raw score dump naar het log channel."""
        try:
            settings = await self.db.get_guild_settings(guild_id)
            log_ch_id = settings.get("log_channel_id")
            if not log_ch_id:
                return
            guild = self.bot.get_guild(guild_id)
            if not guild:
                return
            ch = guild.get_channel(log_ch_id)
            if not ch:
                return

            bm = raw.get("beatmap", {})
            bms = raw.get("beatmapset", {})
            stats = raw.get("statistics", {})
            raw_mods = raw.get("mods", [])

            # Volledige mod dump
            mod_dump = json.dumps(raw_mods, indent=2) if isinstance(raw_mods, list) else str(raw_mods)

            color = 0x50FA7B if is_valid else 0xFF5555
            title = f"📥 Nieuwe score: {username}"
            if slot:
                title += f" [{slot}]"
            if not is_valid:
                title += " ⚠️ ONGELDIG"

            embed = discord.Embed(title=title, color=color, timestamp=datetime.now(timezone.utc))
            embed.add_field(name="Map", value=f"{bms.get('artist','?')} - {bms.get('title','?')} [{bm.get('version','?')}]", inline=False)
            embed.add_field(name="Score", value=f"`{parsed['score']:,}`", inline=True)
            embed.add_field(name="Accuracy", value=f"`{parsed['accuracy']:.2f}%`", inline=True)
            embed.add_field(name="Rank", value=f"`{parsed['rank']}`", inline=True)
            embed.add_field(name="Combo", value=f"`{parsed['max_combo']}x`", inline=True)
            embed.add_field(name="Misses", value=f"`{parsed['count_miss']}`", inline=True)
            embed.add_field(name="Pass", value=f"`{parsed['is_pass']}`", inline=True)
            embed.add_field(name="Parsed mods", value=f"`{parsed['mods']}`", inline=True)
            embed.add_field(name="osu_score_id", value=f"`{parsed['osu_score_id']}`", inline=True)
            embed.add_field(name="beatmap_id", value=f"`{parsed['beatmap_id']}`", inline=True)

            # Raw mods dump (max 1000 chars)
            mod_str = mod_dump[:500] if len(mod_dump) > 500 else mod_dump
            embed.add_field(name="Raw mods (API)", value=f"```json\n{mod_str}\n```", inline=False)

            if not is_valid and reason:
                embed.add_field(name="❌ Reden ongeldig", value=reason, inline=False)

            if slot:
                embed.add_field(name="Pool slot", value=f"`{slot}`", inline=True)

            await ch.send(embed=embed)
        except Exception as e:
            logger.warning(f"discord_log_raw_score mislukt: {e}")

    # ── Tracking loop ──────────────────────────────────────────────────

    async def run_tracking(self, guild_id: int, session_id: int, interval: int, end_after: float = None):
        logger.info(f"[Tracking] ▶️  Gestart voor guild {guild_id}, sessie {session_id}, interval={interval}s")
        await self._discord_log(guild_id, f"▶️ **Tracking gestart** — interval: {interval}s, sessie: `{session_id}`", 0x50FA7B)

        start = asyncio.get_event_loop().time()
        self._active_tasks[guild_id] = asyncio.current_task()

        try:
            poll_count = 0
            while True:
                settings = await self.db.get_guild_settings(guild_id)
                if not settings["tracking_active"]:
                    logger.info(f"[Tracking] ⏹️  Gestopt (tracking_active=False) voor guild {guild_id}")
                    await self._discord_log(guild_id, "⏹️ **Tracking gestopt**", 0xFF5555)
                    break

                if end_after and (asyncio.get_event_loop().time() - start) >= end_after:
                    logger.info(f"[Tracking] ⏰ Tijdsframe verstreken voor guild {guild_id}")
                    await self.db.update_guild_settings(guild_id, tracking_active=False)
                    await self.db.end_tracking_session(session_id)
                    await self._discord_log(guild_id, "⏰ **Tracking automatisch gestopt** — tijdsframe verstreken", 0xFFB86C)
                    guild = self.bot.get_guild(guild_id)
                    if guild and settings["score_channel_id"]:
                        ch = guild.get_channel(settings["score_channel_id"])
                        if ch:
                            await ch.send("⏹️ **Tracking automatisch gestopt** — tijdsframe verstreken.")
                    break

                poll_count += 1
                logger.info(f"[Tracking] 🔄 Poll #{poll_count} voor guild {guild_id}")
                await self._poll_all_players(guild_id, settings)
                logger.info(f"[Tracking] ✅ Poll #{poll_count} klaar. Wacht {interval}s...")
                await asyncio.sleep(interval)

        except asyncio.CancelledError:
            logger.warning(f"[Tracking] ❌ Task gecancelled voor guild {guild_id}")
        except Exception as e:
            logger.exception(f"[Tracking] 💥 Fout voor guild {guild_id}: {e}")
            await self._discord_log(guild_id, f"💥 **Tracking crash:** `{e}`", 0xFF5555)
        finally:
            self._active_tasks.pop(guild_id, None)
            logger.info(f"[Tracking] Task beëindigd voor guild {guild_id}")

    async def _poll_all_players(self, guild_id: int, settings: dict):
        players = await self.db.get_all_players()
        logger.info(f"[Tracking] Polling {len(players)} spelers...")

        guild = self.bot.get_guild(guild_id)
        score_channel = None
        if guild and settings.get("score_channel_id"):
            score_channel = guild.get_channel(settings["score_channel_id"])

        # Pool maps ophalen
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
                count = len(raw_scores) if raw_scores else 0
                logger.info(f"[Tracking] {player['osu_username']}: {count} recente scores van API")

                if not raw_scores:
                    continue

                for raw in raw_scores:
                    parsed = self.osu.parse_score(raw, player["osu_id"], player["discord_id"])
                    if not parsed["osu_score_id"]:
                        logger.warning(f"[Tracking] Score zonder ID geskipped voor {player['osu_username']}")
                        continue

                    exists = await self.db.score_exists(parsed["osu_score_id"])
                    if exists:
                        continue

                    # Bepaal of dit een pool map is en valideer mods
                    bid = parsed["beatmap_id"]
                    slot = None
                    is_valid = True
                    reason = None

                    if bid in pool_map_ids:
                        pool, m = pool_map_lookup[bid]
                        slot = m["slot"]
                        is_valid, reason = validate_mods(parsed["mods"], slot)
                        logger.info(
                            f"[Tracking] Pool score: {player['osu_username']} op {slot} "
                            f"mods={parsed['mods']} valid={is_valid}"
                            + (f" reden={reason}" if not is_valid else "")
                        )

                    parsed["is_valid"] = is_valid
                    parsed["invalid_reason"] = reason
                    await self.db.save_score(parsed)
                    new_scores_total += 1

                    if not is_valid and bid in pool_map_ids:
                        async with self.db.pool.acquire() as conn:
                            await conn.execute(
                                "UPDATE scores SET is_valid=FALSE, invalid_reason=$1 WHERE osu_score_id=$2",
                                reason, parsed["osu_score_id"]
                            )

                    # Log naar Discord log channel (altijd, voor pool maps)
                    if bid in pool_map_ids:
                        await self._discord_log_raw_score(
                            guild_id, player["osu_username"], raw, parsed,
                            slot=slot, is_valid=is_valid, reason=reason
                        )
                        pool_channels_to_refresh.add((pool["channel_id"], pool["id"], pool["name"]))

                    # Score notificatie in score channel
                    if score_channel:
                        await self._post_score_notification(score_channel, parsed, player, raw)

            except Exception as e:
                logger.exception(f"[Tracking] Fout bij {player['osu_username']}: {e}")
                await self._discord_log(guild_id, f"⚠️ Fout bij polling **{player['osu_username']}**: `{e}`", 0xFFB86C)

        logger.info(f"[Tracking] Poll klaar — {new_scores_total} nieuwe scores, {len(pool_channels_to_refresh)} pools refreshen")

        if new_scores_total > 0:
            await self._discord_log(
                guild_id,
                f"🔄 Poll klaar — **{new_scores_total}** nieuwe score(s) opgeslagen",
                0x44475A
            )

        for (ch_id, pool_id, pool_name) in pool_channels_to_refresh:
            if guild:
                ch = guild.get_channel_or_thread(ch_id)
                if ch:
                    admin_cog = self.bot.get_cog("AdminCog")
                    if admin_cog:
                        logger.info(f"[Tracking] Leaderboard refreshen voor pool '{pool_name}'")
                        await admin_cog._update_pool_leaderboard(ch, pool_id, pool_name)

    async def _post_score_notification(self, channel, score: dict, player, raw: dict):
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
        valid_warning = (
            f"\n⚠️ **Telt niet mee:** {score.get('invalid_reason', 'Ongeldige mods')}"
            if not score.get("is_valid", True) else ""
        )

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
