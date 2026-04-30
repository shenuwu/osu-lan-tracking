import discord
from discord import app_commands
from discord.ext import commands
from discord.ext import tasks
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


def format_score(score: int) -> str:
    return f"{score:,}".replace(",", ".")


def format_acc(acc: float) -> str:
    return f"{acc:.2f}%"


def get_channel_or_thread(bot, channel_id: int):
    """Haal channel of thread op — threads zitten in een aparte cache."""
    if not channel_id:
        return None
    return bot.get_channel(channel_id) or discord.utils.get(
        [t for g in bot.guilds for t in g.threads], id=channel_id
    )


class TrackingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._tracking_interval = 60
        self._guild_id = None
        self._session_id = None

    # ── Start / Stop ─────────────────────────────────────────────────────────

    @app_commands.command(name="start_tracking", description="Start het tracken van scores")
    @app_commands.describe(interval="Poll interval in seconden (standaard 60)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def start_tracking(self, interaction: discord.Interaction, interval: int = 60):
        await interaction.response.defer(ephemeral=True)

        settings = await self.bot.db.get_guild_settings(interaction.guild_id)
        if settings["tracking_active"]:
            return await interaction.followup.send("⚠️ Tracking is al actief. Gebruik `/stop_tracking` eerst.")

        players = await self.bot.db.get_all_players()
        if not players:
            return await interaction.followup.send("❌ Geen spelers geregistreerd.")

        self._tracking_interval = max(30, interval)
        self._guild_id = interaction.guild_id

        session = await self.bot.db.create_tracking_session(
            guild_id=interaction.guild_id,
            started_by=interaction.user.id,
            interval=self._tracking_interval
        )
        self._session_id = session["id"]

        await self.bot.db.update_guild_settings(
            interaction.guild_id,
            tracking_active=True,
            tracking_session_id=self._session_id
        )

        self.tracking_loop.change_interval(seconds=self._tracking_interval)
        self.tracking_loop.start()

        await interaction.followup.send(
            f"✅ Tracking gestart! Interval: **{self._tracking_interval}s** • {len(players)} spelers."
        )
        logger.info(f"Tracking gestart — guild {interaction.guild_id}, interval {self._tracking_interval}s")

    @app_commands.command(name="stop_tracking", description="Stop het tracken van scores")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def stop_tracking(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        settings = await self.bot.db.get_guild_settings(interaction.guild_id)
        if not settings["tracking_active"]:
            return await interaction.followup.send("⚠️ Tracking is al gestopt.")

        if self.tracking_loop.is_running():
            self.tracking_loop.stop()

        if self._session_id:
            await self.bot.db.end_tracking_session(self._session_id)

        await self.bot.db.update_guild_settings(interaction.guild_id, tracking_active=False)
        self._session_id = None
        await interaction.followup.send("✅ Tracking gestopt.")

    @app_commands.command(name="force_poll", description="Forceer een directe poll")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def force_poll(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        players = await self.bot.db.get_all_players()
        if not players:
            return await interaction.followup.send("Geen spelers.")

        new_scores = await self._poll_all_players(interaction.guild_id, players)
        await interaction.followup.send(f"✅ Poll klaar — **{new_scores}** nieuwe score(s).")

    @app_commands.command(name="test_tracking", description="Test de API verbinding (geen opslag)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def test_tracking(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        players = await self.bot.db.get_all_players()
        if not players:
            return await interaction.followup.send("Geen spelers.")

        test_player = players[0]
        scores = await self.bot.osu.get_recent_scores(test_player["osu_id"], limit=5)
        if scores is None:
            return await interaction.followup.send("❌ osu! API niet bereikbaar.")

        embed = discord.Embed(
            title="🧪 API Test",
            description=f"Verbinding werkt! Getest op **{test_player['osu_username']}**.",
            color=0x66FF99
        )
        embed.add_field(name="Scores opgehaald", value=str(len(scores)))
        if scores:
            s = self.bot.osu.parse_score(scores[0], test_player["osu_id"])
            embed.add_field(
                name="Laatste score",
                value=f"Beatmap `{s['beatmap_id']}` • {s['client_type']} • mods: `{s['mods']}`"
            )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="tracking_status", description="Huidige tracking status")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def tracking_status(self, interaction: discord.Interaction):
        settings = await self.bot.db.get_guild_settings(interaction.guild_id)
        players = await self.bot.db.get_all_players()

        status = "🟢 Actief" if settings["tracking_active"] else "🔴 Gestopt"
        embed = discord.Embed(
            title="📡 Tracking Status",
            color=0x66FF99 if settings["tracking_active"] else 0xFF6666
        )
        embed.add_field(name="Status", value=status)
        embed.add_field(name="Spelers", value=str(len(players)))
        embed.add_field(name="Interval", value=f"{self._tracking_interval}s")
        if settings.get("score_channel_id"):
            ch = get_channel_or_thread(self.bot, settings["score_channel_id"])
            embed.add_field(name="Score channel", value=ch.mention if ch else f"ID: {settings['score_channel_id']}")
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

                    # Skip als score al bekend is
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
                        }

                    parsed = self.bot.osu.parse_score(
                        raw,
                        osu_id=player["osu_id"],
                        discord_id=player["discord_id"],
                        pool_map=pool_map
                    )

                    score_db_id, is_new = await self.bot.db.save_score(parsed)
                    if not score_db_id:
                        continue

                    if is_new:
                        total_new += 1

                    # Leaderboard update: alleen geldig pool score dat gepasst is
                    if parsed["is_pool_score"] and parsed["is_valid"] and parsed["is_pass"]:
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
                        # Notificatie alleen bij nieuwe scores
                        if score_channel and is_new:
                            await self._send_valid_notification(score_channel, player, parsed, improved)

                    # Ongeldige pool scores: GEEN notificatie in score channel

                await asyncio.sleep(0.5)

            except Exception as e:
                logger.error(f"Fout bij pollen van {player['osu_username']}: {e}", exc_info=True)

        return total_new

    async def _send_valid_notification(self, channel, player, parsed, improved: bool):
        """Stuur notificatie voor een geldig pool score."""
        rank_em = RANK_EMOJIS.get(parsed["rank"], "❓")
        client_badge = "🌐 Lazer" if parsed["client_type"] == "lazer" else "💾 Stable"
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
                f"{client_badge} • `{format_score(parsed['score'])}` • "
                f"{format_acc(parsed['accuracy'])} • {miss_str}"
            ),
            color=color,
            url=f"https://osu.ppy.sh/beatmaps/{parsed['beatmap_id']}"
        )
        embed.set_footer(text=f"osu! LAN Tracker • {parsed['client_type']}")

        try:
            await channel.send(embed=embed)
        except Exception as e:
            logger.error(f"Kon notificatie niet sturen: {e}")

    async def cog_unload(self):
        self.tracking_loop.stop()


async def setup(bot):
    await bot.add_cog(TrackingCog(bot))
