import discord
from discord import app_commands
from discord.ext import commands
from discord.ext import tasks
import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger("tracking")

RANK_EMOJIS = {
    "XH": "🌟", "X": "⭐", "SH": "💿", "S": "💽",
    "A": "🟢", "B": "🔵", "C": "🟡", "D": "🔴", "F": "💀"
}

MOD_CAT_COLORS = {
    "NM": 0x88AAFF,
    "HD": 0xFFDD44,
    "HR": 0xFF4444,
    "DT": 0xAA44FF,
    "FL": 0x333333,
    "EZ": 0x44BB44,
}


def format_score(score: int) -> str:
    return f"{score:,}".replace(",", ".")


def format_acc(acc: float) -> str:
    return f"{acc:.2f}%"


class TrackingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._tracking_interval = 60
        self._guild_id = None
        self._session_id = None

    # ── Start / Stop commands ────────────────────────────────────────────────

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
            return await interaction.followup.send("❌ Geen spelers geregistreerd. Voeg eerst spelers toe.")

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
            f"✅ Tracking gestart! Poll interval: **{self._tracking_interval}s** • "
            f"{len(players)} spelers bijgehouden."
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

        await self.bot.db.update_guild_settings(
            interaction.guild_id,
            tracking_active=False
        )
        self._session_id = None

        await interaction.followup.send("✅ Tracking gestopt.")
        logger.info(f"Tracking gestopt — guild {interaction.guild_id}")

    @app_commands.command(name="force_poll", description="Forceer een directe poll (zonder wachten)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def force_poll(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild_id

        players = await self.bot.db.get_all_players()
        if not players:
            return await interaction.followup.send("Geen spelers om te pollen.")

        new_scores = await self._poll_all_players(guild_id, players)
        await interaction.followup.send(
            f"✅ Poll klaar — **{new_scores}** nieuwe score(s) gevonden."
        )

    @app_commands.command(name="test_tracking", description="Test de API verbinding (geen opslag)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def test_tracking(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        players = await self.bot.db.get_all_players()
        if not players:
            return await interaction.followup.send("Geen spelers om te testen.")

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
            embed.add_field(name="Laatste score", value=f"Beatmap `{s['beatmap_id']}` • {s['client_type']} • mods: `{s['mods']}`")

        await interaction.followup.send(embed=embed)

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
        """Poll alle spelers, sla nieuwe scores op, stuur notificaties. Returns # nieuwe scores."""
        pool_map_index = await self.bot.db.get_all_pool_map_ids()
        settings = await self.bot.db.get_guild_settings(guild_id)
        score_channel_id = settings.get("score_channel_id")
        score_channel = self.bot.get_channel(score_channel_id) if score_channel_id else None

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

                    # Check of map in een pool zit
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

                    row = await self.bot.db.save_score(parsed)
                    if not row:
                        continue

                    total_new += 1

                    # Update pool leaderboard als pool score EN geldig
                    if parsed["is_pool_score"] and parsed["is_valid"] and parsed["is_pass"]:
                        improved = await self.bot.db.update_pool_leaderboard(
                            pool_id=parsed["pool_id"],
                            beatmap_id=parsed["beatmap_id"],
                            discord_id=player["discord_id"],
                            score_row_id=row["id"],
                            score=parsed["score"],
                            accuracy=parsed["accuracy"],
                            mods=parsed["mods"],
                            rank=parsed["rank"],
                            count_miss=parsed["count_miss"]
                        )

                        if score_channel:
                            await self._send_pool_score_notification(
                                score_channel, player, parsed, improved
                            )
                    elif score_channel and parsed["is_pool_score"] and not parsed["is_valid"]:
                        # Ongeldig pool score: stuur kleine warning
                        await self._send_invalid_score_notification(
                            score_channel, player, parsed
                        )

                await asyncio.sleep(0.5)  # Rate limiting

            except Exception as e:
                logger.error(f"Fout bij pollen van {player['osu_username']}: {e}", exc_info=True)

        return total_new

    async def _send_pool_score_notification(self, channel, player, parsed, improved: bool):
        """Stuur een embed notificatie voor een geldig pool score."""
        rank_em = RANK_EMOJIS.get(parsed["rank"], "❓")
        client_badge = "🌐 Lazer" if parsed["client_type"] == "lazer" else "💾 Stable"
        miss_str = f"{parsed['count_miss']}x miss" if parsed["count_miss"] else "FC ✨"
        improvement = "🔼 PR!" if improved else "✅ Score geslagen"

        cat = parsed.get("pool_id")  # we don't have mod_category here directly
        # Haal mod_category op via pool_slot
        slot = parsed.get("pool_slot", "")
        import re
        match = re.match(r"^([A-Z]+)\d+$", slot.upper())
        mod_cat = match.group(1) if match else "NM"
        color = MOD_CAT_COLORS.get(mod_cat, 0xFF66AA)

        embed = discord.Embed(
            title=f"{rank_em} {improvement} — {parsed['mods']}",
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

    async def _send_invalid_score_notification(self, channel, player, parsed):
        """Stuur een kleine warning voor een ongeldig pool score."""
        embed = discord.Embed(
            title=f"⚠️ Ongeldig pool score — {parsed.get('pool_slot', '?')}",
            description=(
                f"**{player['osu_username']}** zette een score op een pool map maar die telt **niet** mee.\n"
                f"Reden: _{parsed.get('invalid_reason', 'onbekend')}_\n"
                f"Mods gebruikt: `{parsed['mods']}`"
            ),
            color=0xFF8800
        )
        try:
            await channel.send(embed=embed)
        except Exception:
            pass

    async def cog_unload(self):
        self.tracking_loop.stop()


async def setup(bot):
    await bot.add_cog(TrackingCog(bot))
