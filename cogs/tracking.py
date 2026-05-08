import os
import discord
from checks import admin_check
from discord import app_commands
from discord.ext import commands
from discord.ext import tasks
from collections import defaultdict
import asyncio
import logging
import re

logger = logging.getLogger("tracking")

RANK_EMOJIS = {
    "XH": "🌟", "X": "⭐", "SH": "💿", "S": "💽",
    "A": "🟢", "B": "🔵", "C": "🟡", "D": "🔴", "F": "💀"
}
MOD_CAT_COLORS = {
    "NM": 0x88AAFF, "HD": 0xFFDD44, "HR": 0xFF4444,
    "DT": 0xAA44FF, "FL": 0x444444, "EZ": 0x44BB44,
}
MOD_CAT_EMOJI = {"NM": "🔵", "HD": "🟡", "HR": "🔴", "DT": "🟣", "FL": "⚫", "EZ": "🟢", "TB": "🏆"}
MEDALS = ["🥇", "🥈", "🥉"]


def fmt_score(score) -> str:
    return f"{int(score):,}".replace(",", ".")

def fmt_acc(acc) -> str:
    return f"{acc:.2f}%"

def medal(i: int) -> str:
    return MEDALS[i] if i < len(MEDALS) else f"`#{i+1}`"

def get_channel_or_thread(bot, channel_id: int):
    if not channel_id:
        return None
    ch = bot.get_channel(channel_id)
    if ch:
        return ch
    for guild in bot.guilds:
        t = guild.get_thread(channel_id)
        if t:
            return t
    return None


class TrackingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._tracking_interval = 60
        self._guild_id = None
        self._session_id = None

    # ── Commands ─────────────────────────────────────────────────────────────

    @app_commands.command(name="start_tracking", description="Start het tracken van scores")
    @app_commands.describe(interval="Poll interval in seconds (default 60)")
    @admin_check()
    async def start_tracking(self, interaction: discord.Interaction, interval: int = 60):
        await interaction.response.defer(ephemeral=True)
        settings = await self.bot.db.get_guild_settings(interaction.guild_id)
        if settings["tracking_active"]:
            return await interaction.followup.send("⚠️ Tracking is already active.")
        players = await self.bot.db.get_all_players()
        if not players:
            return await interaction.followup.send("❌ No players registered.")

        self._tracking_interval = max(30, interval)
        self._guild_id = interaction.guild_id
        session = await self.bot.db.create_tracking_session(
            guild_id=interaction.guild_id, started_by=interaction.user.id, interval=self._tracking_interval
        )
        self._session_id = session["id"]
        await self.bot.db.update_guild_settings(
            interaction.guild_id, tracking_active=True, tracking_session_id=self._session_id
        )
        self.tracking_loop.change_interval(seconds=self._tracking_interval)
        self.tracking_loop.start()
        await interaction.followup.send(f"✅ Tracking started! Interval: **{self._tracking_interval}s** • {len(players)} players.")

    @app_commands.command(name="stop_tracking", description="Stop het tracken van scores")
    @admin_check()
    async def stop_tracking(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        settings = await self.bot.db.get_guild_settings(interaction.guild_id)
        if not settings["tracking_active"]:
            return await interaction.followup.send("⚠️ Tracking is already stopped.")
        if self.tracking_loop.is_running():
            self.tracking_loop.stop()
        if self._session_id:
            await self.bot.db.end_tracking_session(self._session_id)
        await self.bot.db.update_guild_settings(interaction.guild_id, tracking_active=False)
        self._session_id = None
        await interaction.followup.send("✅ Tracking stopped.")

    @app_commands.command(name="force_poll", description="Forceer een directe poll")
    @admin_check()
    async def force_poll(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        players = await self.bot.db.get_all_players()
        if not players:
            return await interaction.followup.send("No players.")
        new_scores = await self._poll_all_players(interaction.guild_id, players)
        await interaction.followup.send(f"✅ Poll done — **{new_scores}** new score(s).")

    @app_commands.command(name="test_tracking", description="Test de API verbinding (geen opslag)")
    @admin_check()
    async def test_tracking(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        players = await self.bot.db.get_all_players()
        if not players:
            return await interaction.followup.send("No players.")
        test_player = players[0]
        scores = await self.bot.osu.get_recent_scores(test_player["osu_id"], limit=3)
        if scores is None:
            return await interaction.followup.send("❌ osu! API not reachable.")
        embed = discord.Embed(title="🧪 API Test", color=0x66FF99)
        embed.add_field(name="Scores fetched", value=str(len(scores)))
        if scores:
            s = self.bot.osu.parse_score(scores[0], test_player["osu_id"])
            embed.add_field(
                name="Latest score",
                value=f"Beatmap `{s['beatmap_id']}` • **{s['client_type']}** • mods: `{s['mods']}`\n"
                      f"score: `{s['score']}` • has_nf: `{s['has_nf']}` • is_pass: `{s['is_pass']}`"
            )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="tracking_status", description="Huidige tracking status")
    @admin_check()
    async def tracking_status(self, interaction: discord.Interaction):
        settings = await self.bot.db.get_guild_settings(interaction.guild_id)
        players = await self.bot.db.get_all_players()
        status = "🟢 Active" if settings["tracking_active"] else "🔴 Stopped"
        embed = discord.Embed(title="📡 Tracking Status", color=0x66FF99 if settings["tracking_active"] else 0xFF6666)
        embed.add_field(name="Status", value=status)
        embed.add_field(name="Players", value=str(len(players)))
        embed.add_field(name="Interval", value=f"{self._tracking_interval}s")
        if settings.get("score_channel_id"):
            ch = get_channel_or_thread(self.bot, settings["score_channel_id"])
            embed.add_field(name="Score channel", value=ch.mention if ch else f"`{settings['score_channel_id']}`")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── Background loop ──────────────────────────────────────────────────────

    @tasks.loop(seconds=60)
    async def tracking_loop(self):
        if not self._guild_id:
            return
        try:
            players = await self.bot.db.get_all_players()
            if not players:
                return
            new = await self._poll_all_players(self._guild_id, players)
            if new > 0:
                logger.info(f"Poll: {new} nieuwe score(s)")
        except Exception as e:
            logger.error(f"Fout in tracking loop: {e}", exc_info=True)

    @tracking_loop.before_loop
    async def before_tracking(self):
        await self.bot.wait_until_ready()

    # ── Core poll logic ──────────────────────────────────────────────────────

    async def _poll_all_players(self, guild_id: int, players) -> int:
        pool_map_index = await self.bot.db.get_all_pool_map_ids()
        settings = await self.bot.db.get_guild_settings(guild_id)
        score_channel = get_channel_or_thread(self.bot, settings.get("score_channel_id"))
        total_new = 0

        for player in players:
            try:
                raw_scores = await self.bot.osu.get_recent_scores(player["osu_id"], limit=50)
                if not raw_scores:
                    continue

                for raw in raw_scores:
                    score_id = raw.get("id")
                    if not score_id:
                        continue
                    if await self.bot.db.score_exists(score_id):
                        continue

                    beatmap = raw.get("beatmap", {})
                    beatmap_id = beatmap.get("id") or raw.get("beatmap_id")

                    pool_info = pool_map_index.get(beatmap_id)
                    pool_map = None
                    if pool_info:
                        pool_map = {
                            "pool_id":      pool_info["pool_id"],
                            "pool_slot":    pool_info["slot"],
                            "mod_category": pool_info["mod_category"] or "NM",
                            "max_combo":    pool_info["max_combo"] or 0,
                            "total_length": pool_info["total_length"] or 0,
                        }

                    parsed = self.bot.osu.parse_score(
                        raw, osu_id=player["osu_id"],
                        discord_id=player["discord_id"], pool_map=pool_map
                    )

                    # Als score=0 (lazer): gebruik bot OAuth token voor echte score
                    if parsed["client_type"] == "lazer" and parsed["score"] == 0:
                        oauth = await self.bot.db.get_oauth_token(0)  # 0 = bot token
                        if oauth:
                            try:
                                from datetime import datetime, timezone, timedelta
                                expires = oauth["expires_at"]
                                if hasattr(expires, 'replace'):
                                    expires = expires.replace(tzinfo=timezone.utc) if expires.tzinfo is None else expires
                                if expires < datetime.now(timezone.utc):
                                    new_token = await self.bot.osu.refresh_user_token(oauth["refresh_token"])
                                    if new_token:
                                        new_expires = datetime.now(timezone.utc) + timedelta(seconds=new_token.get("expires_in", 86400))
                                        await self.bot.db.save_oauth_token(
                                            0, oauth["osu_id"],
                                            new_token["access_token"], new_token["refresh_token"], new_expires
                                        )
                                        oauth = await self.bot.db.get_oauth_token(0)

                                detail = await self.bot.osu.get_score_with_token(score_id, oauth["access_token"])
                                if detail:
                                    real = detail.get("total_score") or detail.get("score") or 0
                                    if real > 0:
                                        parsed["score"] = real * 2  # NF halveert score, x2 voor max 1M
                                        logger.info(f"OAuth score: {player['osu_username']} total_score={real} final={real*2}")
                            except Exception as e:
                                logger.error(f"OAuth score fetch gefaald: {e}")

                    score_db_id, is_new = await self.bot.db.save_score(parsed)
                    if not score_db_id:
                        logger.warning(f"save_score gaf geen id terug voor score {score_id}")
                        continue

                    # Update score in DB als het als 0 was opgeslagen maar nu correct is
                    if parsed["score"] > 0:
                        await self.bot.db.update_score_value(score_db_id, parsed["score"])

                    if is_new:
                        total_new += 1

                    # Leaderboard: alleen lazer + geldig + gepasst
                    if (parsed["is_pool_score"]
                            and parsed["is_valid"]
                            and parsed["is_pass"]
                            and parsed["client_type"] == "lazer"):
                        logger.info(
                            f"Pool score: {player['osu_username']} op {parsed['pool_slot']} "
                            f"score={parsed['score']} acc={parsed['accuracy']} mods={parsed['mods']}"
                        )
                        try:
                            improved = await self.bot.db.update_pool_leaderboard(
                                pool_id=parsed["pool_id"],
                                beatmap_id=parsed["beatmap_id"],
                                discord_id=player["discord_id"],
                                score_row_id=score_db_id,
                                score=parsed["score"],
                                accuracy=parsed["accuracy"],
                                mods=parsed["mods"],
                                rank=parsed["rank"],
                                count_miss=parsed["count_miss"]
                            )
                            logger.info(
                                f"Leaderboard: {player['osu_username']} op {parsed['pool_slot']} "
                                f"score={parsed['score']} improved={improved}"
                            )
                        except Exception as e:
                            logger.error(f"update_pool_leaderboard gefaald: {e}", exc_info=True)
                            improved = False

                        if score_channel and is_new:
                            await self._send_score_notification(score_channel, player, parsed, improved)

                        if is_new:
                            await self._update_thread_leaderboard(parsed["pool_id"])
                            # Dashboard bijwerken
                            dashboard = self.bot.cogs.get("DashboardCog")
                            if dashboard:
                                await dashboard.update_dashboard(guild_id)

                await asyncio.sleep(0.5)

            except Exception as e:
                logger.error(f"Fout bij pollen van {player['osu_username']}: {e}", exc_info=True)

        # Dashboard altijd updaten als er nieuwe scores zijn
        if total_new > 0:
            dashboard = self.bot.cogs.get("DashboardCog")
            if dashboard:
                try:
                    await dashboard.update_dashboard(guild_id)
                except Exception as e:
                    logger.error(f"Dashboard update gefaald: {e}", exc_info=True)

        return total_new

    async def _send_score_notification(self, channel, player, parsed, improved: bool):
        rank_em = RANK_EMOJIS.get(parsed["rank"], "❓")
        miss_str = f"{parsed['count_miss']}x miss" if parsed["count_miss"] else "FC ✨"
        title_str = "🔼 PR!" if improved else "✅ Score"
        slot = parsed.get("pool_slot", "")
        match = re.match(r"^([A-Z]+)\d+$", slot.upper())
        mod_cat = match.group(1) if match else "NM"
        color = MOD_CAT_COLORS.get(mod_cat, 0xFF66AA)

        embed = discord.Embed(
            title=f"{rank_em} {title_str} — {parsed['mods']}",
            description=(
                f"**{player['osu_username']}** op `{slot}`\n"
                f"🌐 Lazer • `{fmt_score(parsed['score'])}` • {fmt_acc(parsed['accuracy'])} • {miss_str}"
            ),
            color=color,
            url=f"https://osu.ppy.sh/beatmaps/{parsed['beatmap_id']}"
        )
        embed.set_footer(text="osu! LAN Tracker • lazer")
        try:
            await channel.send(embed=embed)
        except Exception as e:
            logger.error(f"Kon score notificatie niet sturen: {e}")

    async def _update_thread_leaderboard(self, pool_id: int):
        """Edit het leaderboard bericht in de pool thread, of stuur nieuw als het niet bestaat."""
        try:
            pool = await self.bot.db.get_pool_by_id(pool_id)
            if not pool:
                return

            thread = get_channel_or_thread(self.bot, pool["channel_id"])
            if not thread:
                logger.warning(f"Pool thread {pool['channel_id']} not found in cache")
                return

            rows = await self.bot.db.get_pool_leaderboard(pool_id)
            maps_all = await self.bot.db.get_pool_maps(pool_id)

            lb_by_map = defaultdict(list)
            for r in rows:
                lb_by_map[r["beatmap_id"]].append(r)

            categories = {}
            for m in maps_all:
                categories.setdefault(m["mod_category"] or "?", []).append(m)

            embed = discord.Embed(title=f"🎵 {pool['name']}", color=0xFF66AA)
            embed.set_footer(text=f"{len(maps_all)} maps • NF required on all slots")

            for cat in ["NM", "HD", "HR", "DT", "FL", "EZ", "TB", "?"]:
                if cat not in categories:
                    continue
                lines = []
                for m in sorted(categories[cat], key=lambda x: x["slot"]):
                    map_line = (
                        f"`{m['slot']}` **[{m['artist']} - {m['title']} [{m['version']}]]"
                        f"(https://osu.ppy.sh/beatmaps/{m['beatmap_id']})**"
                    )
                    entries = lb_by_map.get(m["beatmap_id"], [])
                    if entries:
                        top = entries[0]
                        miss_str = "FC ✨" if not top["count_miss"] else f"{top['count_miss']}x miss"
                        map_line += f"\n  🥇 **{top['osu_username']}** — `{fmt_score(top['score'])}` • {fmt_acc(top['accuracy'])} • {miss_str}"
                    else:
                        map_line += "\n  _(no scores yet)_"
                    lines.append(map_line)
                embed.add_field(
                    name=f"{MOD_CAT_EMOJI.get(cat, '⚪')} {cat}",
                    value="\n".join(lines),
                    inline=False
                )

            # Edit bestaand bericht of stuur nieuw
            existing_msg_id = pool.get("leaderboard_message_id")
            if existing_msg_id:
                try:
                    msg = await thread.fetch_message(existing_msg_id)
                    await msg.edit(embed=embed)
                    return
                except (discord.NotFound, discord.HTTPException):
                    pass  # Bericht weg, stuur nieuw

            msg = await thread.send(embed=embed)
            await self.bot.db.save_leaderboard_message_id(pool_id, msg.id)

        except Exception as e:
            logger.error(f"_update_thread_leaderboard gefaald: {e}", exc_info=True)

    async def cog_unload(self):
        self.tracking_loop.stop()


async def setup(bot):
    await bot.add_cog(TrackingCog(bot))
