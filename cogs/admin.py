import discord
from discord.ext import commands
from discord import app_commands
import asyncio
from datetime import datetime, timezone
from database import Database
from osu_api import OsuAPI

# Shared instances worden via bot doorgegeven
def get_db(bot) -> Database:
    return bot.db

def get_osu(bot) -> OsuAPI:
    return bot.osu


def is_admin():
    async def predicate(interaction: discord.Interaction):
        return interaction.user.guild_permissions.administrator
    return app_commands.check(predicate)


class AdminCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @property
    def db(self) -> Database:
        return self.bot.db

    @property
    def osu(self) -> OsuAPI:
        return self.bot.osu

    # ── Player management ──────────────────────────────────────────────

    @app_commands.command(name="add_player", description="[Admin] Voeg een speler toe aan de LAN tracker")
    @app_commands.describe(member="Discord member", osu_username="osu! gebruikersnaam")
    @is_admin()
    async def add_player(self, interaction: discord.Interaction, member: discord.Member, osu_username: str):
        await interaction.response.defer(ephemeral=True)
        user_data = await self.osu.get_user(osu_username)
        if not user_data:
            return await interaction.followup.send(f"❌ osu! gebruiker `{osu_username}` niet gevonden.", ephemeral=True)
        await self.db.add_player(member.id, user_data["username"], user_data["id"], interaction.user.id)
        await interaction.followup.send(
            f"✅ **{member.display_name}** gekoppeld aan osu! account **{user_data['username']}** (ID: {user_data['id']})",
            ephemeral=True
        )

    @app_commands.command(name="remove_player", description="[Admin] Verwijder een speler uit de LAN tracker")
    @is_admin()
    async def remove_player(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        player = await self.db.get_player(member.id)
        if not player:
            return await interaction.followup.send("❌ Speler niet gevonden in de database.", ephemeral=True)
        await self.db.remove_player(member.id)
        await interaction.followup.send(f"✅ **{player['osu_username']}** verwijderd uit de tracker.", ephemeral=True)

    @app_commands.command(name="list_players", description="[Admin] Bekijk alle geregistreerde spelers")
    @is_admin()
    async def list_players(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        players = await self.db.get_all_players()
        if not players:
            return await interaction.followup.send("Geen spelers geregistreerd.", ephemeral=True)
        lines = [f"`{i+1}.` **{p['osu_username']}** — <@{p['discord_id']}>" for i, p in enumerate(players)]
        embed = discord.Embed(title="🎮 Geregistreerde Spelers", description="\n".join(lines), color=0xFF79C6)
        embed.set_footer(text=f"{len(players)} spelers totaal")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── Pool management ────────────────────────────────────────────────

    @app_commands.command(name="create_pool", description="[Admin] Maak een mappool channel aan")
    @app_commands.describe(name="Naam van de pool (bijv. NM, HD, HR)", category="Optionele categorie voor het channel")
    @is_admin()
    async def create_pool(self, interaction: discord.Interaction, name: str, category: discord.CategoryChannel = None):
        await interaction.response.defer(ephemeral=True)
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(send_messages=False),
            interaction.guild.me: discord.PermissionOverwrite(send_messages=True, manage_messages=True)
        }
        channel = await interaction.guild.create_text_channel(
            name=f"pool-{name.lower()}",
            category=category,
            overwrites=overwrites,
            topic=f"Leaderboard voor mappool: {name}"
        )
        pool = await self.db.create_pool(name, channel.id, interaction.guild.id, interaction.user.id)
        embed = discord.Embed(
            title=f"✅ Pool '{name}' aangemaakt",
            description=f"Channel: {channel.mention}\nPool ID: `{pool['id']}`\n\nGebruik `/add_map` om maps toe te voegen.",
            color=0x50FA7B
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="add_map", description="[Admin] Voeg een beatmap toe aan een pool")
    @app_commands.describe(pool_channel="Het pool channel", beatmap_id="osu! beatmap ID", slot="Bijv. NM1, HD2, HR1")
    @is_admin()
    async def add_map(self, interaction: discord.Interaction, pool_channel: discord.TextChannel, beatmap_id: int, slot: str):
        await interaction.response.defer(ephemeral=True)
        pool = await self.db.get_pool_by_channel(pool_channel.id)
        if not pool:
            return await interaction.followup.send("❌ Dat channel is geen geregistreerde pool.", ephemeral=True)
        bm = await self.osu.get_beatmap(beatmap_id)
        if not bm:
            return await interaction.followup.send(f"❌ Beatmap `{beatmap_id}` niet gevonden.", ephemeral=True)
        bms = bm.get("beatmapset", {})
        await self.db.add_map_to_pool(
            pool["id"], beatmap_id, bms.get("id"),
            bms.get("title", "Unknown"), bms.get("artist", "Unknown"),
            bm.get("version", "Unknown"), slot.upper()
        )
        await interaction.followup.send(
            f"✅ **{slot.upper()}** — {bms.get('artist')} - {bms.get('title')} [{bm.get('version')}] toegevoegd aan pool **{pool['name']}**",
            ephemeral=True
        )
        # Refresh leaderboard
        await self._update_pool_leaderboard(pool_channel, pool["id"], pool["name"])

    @app_commands.command(name="remove_map", description="[Admin] Verwijder een beatmap uit een pool")
    @is_admin()
    async def remove_map(self, interaction: discord.Interaction, pool_channel: discord.TextChannel, beatmap_id: int):
        await interaction.response.defer(ephemeral=True)
        pool = await self.db.get_pool_by_channel(pool_channel.id)
        if not pool:
            return await interaction.followup.send("❌ Geen pool gevonden voor dit channel.", ephemeral=True)
        await self.db.remove_map_from_pool(pool["id"], beatmap_id)
        await interaction.followup.send(f"✅ Beatmap `{beatmap_id}` verwijderd uit pool **{pool['name']}**.", ephemeral=True)
        await self._update_pool_leaderboard(pool_channel, pool["id"], pool["name"])

    @app_commands.command(name="refresh_leaderboard", description="[Admin] Herlaad het leaderboard van een pool channel")
    @is_admin()
    async def refresh_leaderboard(self, interaction: discord.Interaction, pool_channel: discord.TextChannel):
        await interaction.response.defer(ephemeral=True)
        pool = await self.db.get_pool_by_channel(pool_channel.id)
        if not pool:
            return await interaction.followup.send("❌ Geen pool gevonden voor dit channel.", ephemeral=True)
        await self._update_pool_leaderboard(pool_channel, pool["id"], pool["name"])
        await interaction.followup.send(f"✅ Leaderboard van **{pool['name']}** vernieuwd.", ephemeral=True)

    async def _update_pool_leaderboard(self, channel: discord.TextChannel, pool_id: int, pool_name: str):
        maps = await self.db.get_pool_maps(pool_id)
        scores_raw = await self.db.get_pool_leaderboard(pool_id)

        # Groepeer scores per beatmap
        scores_by_map = {}
        for s in scores_raw:
            bid = s["beatmap_id"]
            if bid not in scores_by_map:
                scores_by_map[bid] = []
            scores_by_map[bid].append(s)

        embeds = []
        rank_emojis = ["🥇", "🥈", "🥉"]

        header = discord.Embed(
            title=f"📋 Pool: {pool_name}",
            description=f"**{len(maps)} maps** in deze pool\nAutomatisch bijgewerkt bij nieuwe scores",
            color=0xBD93F9
        )
        header.set_footer(text=f"Laatste update: {datetime.now().strftime('%d-%m-%Y %H:%M')}")
        embeds.append(header)

        for m in maps:
            bid = m["beatmap_id"]
            map_scores = scores_by_map.get(bid, [])
            lines = []
            for i, s in enumerate(map_scores[:10]):
                emoji = rank_emojis[i] if i < 3 else f"`#{i+1}`"
                acc = f"{s['accuracy']:.2f}%"
                combo = f"{s['max_combo']}x"
                mods = f"[{s['mods']}]" if s['mods'] != 'NM' else ""
                lines.append(f"{emoji} **{s['osu_username']}** — `{s['score']:,}` | {acc} | {combo} {mods}")

            desc = "\n".join(lines) if lines else "*Nog geen scores*"
            embed = discord.Embed(
                title=f"**{m['slot']}** — {m['artist']} - {m['title']} [{m['version']}]",
                description=desc,
                color=0x44475A
            )
            embed.set_footer(text=f"beatmap_id: {bid}")
            embeds.append(embed)

        # Verwijder oude messages
        await channel.purge(limit=100)

        # Stuur nieuwe embeds (max 10 per keer)
        for i in range(0, len(embeds), 10):
            await channel.send(embeds=embeds[i:i+10])

    # ── Tracking control ──────────────────────────────────────────────

    @app_commands.command(name="start_tracking", description="[Admin] Start score tracking voor alle geregistreerde spelers")
    @app_commands.describe(interval="Polling interval in seconden (standaard 60)", timeframe_hours="Hoelang tracken in uren (0 = oneindig)")
    @is_admin()
    async def start_tracking(self, interaction: discord.Interaction, interval: int = 60, timeframe_hours: float = 0.0):
        await interaction.response.defer(ephemeral=True)
        settings = await self.db.get_guild_settings(interaction.guild.id)
        if settings["tracking_active"]:
            return await interaction.followup.send("⚠️ Tracking is al actief. Gebruik `/stop_tracking` eerst.", ephemeral=True)

        session = await self.db.create_tracking_session(
            interaction.guild.id, interaction.user.id, datetime.now(timezone.utc), interval
        )
        await self.db.update_guild_settings(interaction.guild.id, tracking_active=True, tracking_session_id=session["id"])

        embed = discord.Embed(
            title="▶️ Tracking gestart",
            description=f"**Interval:** {interval}s\n**Tijdsframe:** {'Oneindig' if timeframe_hours == 0 else f'{timeframe_hours}u'}\n**Sessie ID:** `{session['id']}`",
            color=0x50FA7B
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

        end_after = timeframe_hours * 3600 if timeframe_hours > 0 else None
        self.bot.loop.create_task(
            self.bot.get_cog("TrackingCog").run_tracking(interaction.guild.id, session["id"], interval, end_after)
        )

    @app_commands.command(name="stop_tracking", description="[Admin] Stop de actieve score tracking")
    @is_admin()
    async def stop_tracking(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        settings = await self.db.get_guild_settings(interaction.guild.id)
        if not settings["tracking_active"]:
            return await interaction.followup.send("⚠️ Tracking is niet actief.", ephemeral=True)
        await self.db.update_guild_settings(interaction.guild.id, tracking_active=False)
        if settings["tracking_session_id"]:
            await self.db.end_tracking_session(settings["tracking_session_id"])
        await interaction.followup.send("⏹️ Tracking gestopt.", ephemeral=True)

    @app_commands.command(name="test_tracking", description="[Admin] Test tracking: poll scores 1x en log resultaten")
    @is_admin()
    async def test_tracking(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        players = await self.db.get_all_players()
        if not players:
            return await interaction.followup.send("❌ Geen spelers geregistreerd.", ephemeral=True)

        results = []
        for player in players:
            scores = await self.osu.get_recent_scores(player["osu_id"], limit=5)
            count = len(scores) if scores else 0
            results.append(f"**{player['osu_username']}**: {count} recente scores gevonden")

        embed = discord.Embed(
            title="🧪 Test Tracking Resultaat",
            description="\n".join(results),
            color=0xFFB86C
        )
        embed.set_footer(text="Dit is een test — geen scores opgeslagen")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="set_score_channel", description="[Admin] Stel het channel in waar nieuwe scores gepost worden")
    @is_admin()
    async def set_score_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await self.db.update_guild_settings(interaction.guild.id, score_channel_id=channel.id)
        await interaction.response.send_message(f"✅ Score channel ingesteld op {channel.mention}", ephemeral=True)

    @app_commands.command(name="tracking_status", description="[Admin] Bekijk de huidige tracking status")
    @is_admin()
    async def tracking_status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        settings = await self.db.get_guild_settings(interaction.guild.id)
        players = await self.db.get_all_players()
        status = "🟢 Actief" if settings["tracking_active"] else "🔴 Gestopt"
        embed = discord.Embed(title="📡 Tracking Status", color=0x8BE9FD)
        embed.add_field(name="Status", value=status, inline=True)
        embed.add_field(name="Geregistreerde spelers", value=str(len(players)), inline=True)
        embed.add_field(name="Score channel", value=f"<#{settings['score_channel_id']}>" if settings["score_channel_id"] else "Niet ingesteld", inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot):
    bot.db = Database()
    await bot.db.init()
    bot.osu = OsuAPI()
    await bot.osu.get_token()
    await bot.add_cog(AdminCog(bot))
