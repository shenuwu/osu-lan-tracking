import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import logging
from datetime import datetime, timezone
from database import Database
from osu_api import OsuAPI
from mod_validator import describe_required_mods

logger = logging.getLogger("admin")

# Volgorde van slot categorieën in de pool channel (van boven naar beneden)
SLOT_ORDER = ["NM", "HD", "HR", "DT", "FM", "EX", "TB"]

# Mod-info labels per slot categorie
SLOT_MOD_LABELS = {
    "NM": "🎵 ScoreV2 (SM) + NoFail verplicht",
    "HD": "🔵 ScoreV2 (SM) + NoFail + Hidden verplicht",
    "HR": "🔴 ScoreV2 (SM) + NoFail + HardRock verplicht",
    "DT": "⚡ ScoreV2 (SM) + NoFail + DoubleTime verplicht",
    "FM": "🆓 ScoreV2 (SM) + NoFail verplicht — overige mods vrij (geen EZ/HT)",
    "EX": "🌟 Extra — alles toegestaan",
    "TB": "🏆 Tiebreaker — ScoreV2 (SM) + NoFail, overige mods vrij (geen EZ/HT)",
}

def get_slot_category(slot: str) -> str:
    return "".join(c for c in slot.upper() if c.isalpha())

def slot_sort_key(m) -> int:
    cat = get_slot_category(m["slot"])
    try:
        base = SLOT_ORDER.index(cat) * 100
    except ValueError:
        base = 999
    # Sorteer ook op het getal achter de categorie (NM1, NM2, ...)
    num_part = "".join(c for c in m["slot"] if c.isdigit())
    num = int(num_part) if num_part else 0
    return base + num


def get_db(bot) -> Database:
    return bot.db

def get_osu(bot) -> OsuAPI:
    return bot.osu


def is_admin():
    async def predicate(interaction: discord.Interaction):
        return interaction.user.guild_permissions.administrator
    return app_commands.check(predicate)


async def pool_autocomplete(interaction: discord.Interaction, current: str):
    """Autocomplete die alle pools voor de huidige guild teruggeeft."""
    try:
        pools = await interaction.client.db.get_all_pools(interaction.guild.id)
        return [
            app_commands.Choice(name=p["name"], value=str(p["id"]))
            for p in pools
            if current.lower() in p["name"].lower()
        ][:25]
    except Exception:
        return []


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
        logger.info(f"Speler toegevoegd: {user_data['username']} (osu_id={user_data['id']}) door {interaction.user}")
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
        logger.info(f"Speler verwijderd: {player['osu_username']} door {interaction.user}")
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

    @app_commands.command(name="create_pool", description="[Admin] Maak een mappool thread aan")
    @app_commands.describe(name="Naam van de pool (bijv. NM, HD, HR)", parent_channel="Channel waar de thread in aangemaakt wordt")
    @is_admin()
    async def create_pool(self, interaction: discord.Interaction, name: str, parent_channel: discord.TextChannel):
        await interaction.response.defer(ephemeral=True)
        # Stuur een bericht in het parent channel en maak daar een thread van
        msg = await parent_channel.send(f"📋 **Pool: {name}** — leaderboard thread")
        thread = await msg.create_thread(
            name=f"pool-{name.lower()}",
            auto_archive_duration=10080,  # 7 dagen
        )
        pool = await self.db.create_pool(name, thread.id, interaction.guild.id, interaction.user.id)
        logger.info(f"Pool thread aangemaakt: '{name}' (id={pool['id']}) als thread #{thread.name}")
        embed = discord.Embed(
            title=f"✅ Pool '{name}' aangemaakt",
            description=f"Thread: {thread.mention}\nPool ID: `{pool['id']}`\n\nGebruik `/add_map` om maps toe te voegen.",
            color=0x50FA7B
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="add_map", description="[Admin] Voeg een beatmap toe aan een pool")
    @app_commands.describe(pool="Pool naam (kies uit de lijst)", beatmap_id="osu! beatmap ID", slot="Bijv. NM1, HD2, HR1")
    @app_commands.autocomplete(pool=pool_autocomplete)
    @is_admin()
    async def add_map(self, interaction: discord.Interaction, pool: str, beatmap_id: int, slot: str):
        await interaction.response.defer(ephemeral=True)
        pool_row = await self.db.get_pool_by_id(int(pool))
        if not pool_row:
            return await interaction.followup.send("❌ Pool niet gevonden.", ephemeral=True)
        bm = await self.osu.get_beatmap(beatmap_id)
        if not bm:
            return await interaction.followup.send(f"❌ Beatmap `{beatmap_id}` niet gevonden.", ephemeral=True)
        bms = bm.get("beatmapset", {})
        await self.db.add_map_to_pool(
            pool_row["id"], beatmap_id, bms.get("id"),
            bms.get("title", "Unknown"), bms.get("artist", "Unknown"),
            bm.get("version", "Unknown"), slot.upper()
        )
        logger.info(f"Map toegevoegd: {slot.upper()} beatmap_id={beatmap_id} aan pool '{pool_row['name']}'")
        await interaction.followup.send(
            f"✅ **{slot.upper()}** — {bms.get('artist')} - {bms.get('title')} [{bm.get('version')}] toegevoegd aan pool **{pool_row['name']}**",
            ephemeral=True
        )
        thread = interaction.guild.get_channel_or_thread(pool_row["channel_id"])
        if thread:
            await self._update_pool_leaderboard(thread, pool_row["id"], pool_row["name"])

    @app_commands.command(name="remove_map", description="[Admin] Verwijder een beatmap uit een pool")
    @app_commands.describe(pool="Pool naam (kies uit de lijst)", beatmap_id="osu! beatmap ID")
    @app_commands.autocomplete(pool=pool_autocomplete)
    @is_admin()
    async def remove_map(self, interaction: discord.Interaction, pool: str, beatmap_id: int):
        await interaction.response.defer(ephemeral=True)
        pool_row = await self.db.get_pool_by_id(int(pool))
        if not pool_row:
            return await interaction.followup.send("❌ Pool niet gevonden.", ephemeral=True)
        await self.db.remove_map_from_pool(pool_row["id"], beatmap_id)
        await interaction.followup.send(f"✅ Beatmap `{beatmap_id}` verwijderd uit pool **{pool_row['name']}**.", ephemeral=True)
        thread = interaction.guild.get_channel_or_thread(pool_row["channel_id"])
        if thread:
            await self._update_pool_leaderboard(thread, pool_row["id"], pool_row["name"])

    @app_commands.command(name="refresh_leaderboard", description="[Admin] Herlaad het leaderboard van een pool")
    @app_commands.describe(pool="Pool naam (kies uit de lijst)")
    @app_commands.autocomplete(pool=pool_autocomplete)
    @is_admin()
    async def refresh_leaderboard(self, interaction: discord.Interaction, pool: str):
        await interaction.response.defer(ephemeral=True)
        pool_row = await self.db.get_pool_by_id(int(pool))
        if not pool_row:
            return await interaction.followup.send("❌ Pool niet gevonden.", ephemeral=True)
        thread = interaction.guild.get_channel_or_thread(pool_row["channel_id"])
        if not thread:
            return await interaction.followup.send("❌ Thread niet gevonden. Is de thread gearchiveerd?", ephemeral=True)
        await self._update_pool_leaderboard(thread, pool_row["id"], pool_row["name"])
        await interaction.followup.send(f"✅ Leaderboard van **{pool_row['name']}** vernieuwd.", ephemeral=True)

    async def _update_pool_leaderboard(self, channel: discord.TextChannel, pool_id: int, pool_name: str):
        maps = await self.db.get_pool_maps(pool_id)
        scores_raw = await self.db.get_pool_leaderboard(pool_id)

        # Sorteer maps op SLOT_ORDER (NM→HD→HR→DT→FM→EX→TB) en daarna op slotnummer
        maps = sorted(maps, key=slot_sort_key)

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

            cat = get_slot_category(m["slot"])
            mod_label = SLOT_MOD_LABELS.get(cat, describe_required_mods(m["slot"]))
            required_mods = describe_required_mods(m["slot"])

            embed = discord.Embed(
                title=f"**{m['slot']}** — {m['artist']} - {m['title']} [{m['version']}]",
                description=desc,
                color=0x44475A
            )
            embed.set_footer(text=f"{mod_label} ({required_mods}) • beatmap_id: {bid}")
            embeds.append(embed)

        # Verwijder oude messages (threads hebben geen purge, dus handmatig)
        try:
            async for msg in channel.history(limit=100):
                try:
                    await msg.delete()
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"Kon oude berichten niet verwijderen: {e}")

        # Stuur nieuwe embeds (max 10 per keer)
        for i in range(0, len(embeds), 10):
            await channel.send(embeds=embeds[i:i+10])

        logger.info(f"Leaderboard bijgewerkt voor pool '{pool_name}' ({len(maps)} maps, {len(embeds)-1} embeds)")

    # ── Tracking control ──────────────────────────────────────────────

    @app_commands.command(name="start_tracking", description="[Admin] Start score tracking voor alle geregistreerde spelers")
    @app_commands.describe(interval="Polling interval in seconden (standaard 60)", timeframe_hours="Hoelang tracken in uren (0 = oneindig)")
    @is_admin()
    async def start_tracking(self, interaction: discord.Interaction, interval: int = 60, timeframe_hours: float = 0.0):
        await interaction.response.defer(ephemeral=True)

        logger.info(f"start_tracking aangeroepen door {interaction.user} — interval={interval}s, timeframe={timeframe_hours}u")

        settings = await self.db.get_guild_settings(interaction.guild.id)
        if settings["tracking_active"]:
            logger.warning("start_tracking: tracking is al actief")
            return await interaction.followup.send("⚠️ Tracking is al actief. Gebruik `/stop_tracking` eerst.", ephemeral=True)

        players = await self.db.get_all_players()
        if not players:
            logger.warning("start_tracking: geen spelers geregistreerd")
            return await interaction.followup.send("❌ Geen spelers geregistreerd. Voeg eerst spelers toe met `/add_player`.", ephemeral=True)

        logger.info(f"start_tracking: {len(players)} spelers gevonden, sessie aanmaken...")

        session = await self.db.create_tracking_session(
            interaction.guild.id, interaction.user.id, interval
        )
        await self.db.update_guild_settings(interaction.guild.id, tracking_active=True, tracking_session_id=session["id"])
        logger.info(f"start_tracking: sessie aangemaakt id={session['id']}, tracking_active=True gezet")

        tracking_cog = self.bot.get_cog("TrackingCog")
        if not tracking_cog:
            logger.error("start_tracking: TrackingCog niet gevonden!")
            await interaction.followup.send("❌ TrackingCog is niet geladen. Herstart de bot.", ephemeral=True)
            return

        end_after = timeframe_hours * 3600 if timeframe_hours > 0 else None

        # Gebruik bot.loop.create_task — dit is de betrouwbare manier
        task = self.bot.loop.create_task(
            tracking_cog.run_tracking(interaction.guild.id, session["id"], interval, end_after)
        )
        logger.info(f"start_tracking: tracking task aangemaakt ({task})")

        embed = discord.Embed(
            title="▶️ Tracking gestart",
            description=(
                f"**Interval:** {interval}s\n"
                f"**Tijdsframe:** {'Oneindig' if timeframe_hours == 0 else f'{timeframe_hours}u'}\n"
                f"**Sessie ID:** `{session['id']}`\n"
                f"**Spelers:** {len(players)}"
            ),
            color=0x50FA7B
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

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
        logger.info(f"Tracking gestopt door {interaction.user}")
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
            logger.info(f"[TestTracking] {player['osu_username']}: {count} scores")

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
        logger.info(f"Score channel ingesteld op #{channel.name} door {interaction.user}")
        await interaction.response.send_message(f"✅ Score channel ingesteld op {channel.mention}", ephemeral=True)

    @app_commands.command(name="tracking_status", description="[Admin] Bekijk de huidige tracking status")
    @is_admin()
    async def tracking_status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        settings = await self.db.get_guild_settings(interaction.guild.id)
        players = await self.db.get_all_players()

        tracking_cog = self.bot.get_cog("TrackingCog")
        has_active_task = interaction.guild.id in (tracking_cog._active_tasks if tracking_cog else {})

        status = "🟢 Actief" if settings["tracking_active"] else "🔴 Gestopt"
        task_status = "✅ Task draait" if has_active_task else "⚠️ Geen actieve task"

        embed = discord.Embed(title="📡 Tracking Status", color=0x8BE9FD)
        embed.add_field(name="Status", value=status, inline=True)
        embed.add_field(name="Task", value=task_status, inline=True)
        embed.add_field(name="Geregistreerde spelers", value=str(len(players)), inline=True)
        embed.add_field(name="Score channel", value=f"<#{settings['score_channel_id']}>" if settings["score_channel_id"] else "Niet ingesteld", inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)


    @app_commands.command(name="delete_pool", description="[Admin] Verwijder een mappool volledig")
    @app_commands.describe(pool="Pool naam (kies uit de lijst)")
    @app_commands.autocomplete(pool=pool_autocomplete)
    @is_admin()
    async def delete_pool(self, interaction: discord.Interaction, pool: str):
        await interaction.response.defer(ephemeral=True)
        pool_row = await self.db.get_pool_by_id(int(pool))
        if not pool_row:
            return await interaction.followup.send("❌ Pool niet gevonden.", ephemeral=True)

        pool_name = pool_row["name"]
        thread = interaction.guild.get_channel_or_thread(pool_row["channel_id"])

        await self.db.delete_pool(pool_row["id"])
        logger.info(f"Pool '{pool_name}' verwijderd door {interaction.user}")

        msg = f"✅ Pool **{pool_name}** verwijderd uit de database."
        if thread:
            try:
                await thread.delete()
                msg += " Thread ook verwijderd."
            except Exception:
                msg += " ⚠️ Thread kon niet verwijderd worden (al weg?)."

        await interaction.followup.send(msg, ephemeral=True)

    @app_commands.command(name="set_log_channel", description="[Admin] Stel het channel in voor bot logs en score debug info")
    @is_admin()
    async def set_log_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await self.db.update_guild_settings(interaction.guild.id, log_channel_id=channel.id)
        logger.info(f"Log channel ingesteld op #{channel.name} door {interaction.user}")
        await interaction.response.send_message(
            f"✅ Log channel ingesteld op {channel.mention}\n"
            "Alle tracking logs, raw API scores en fouten komen hier.",
            ephemeral=True
        )

    @app_commands.command(name="check_score", description="[Admin] Debug: bekijk een opgeslagen score op osu! score ID")
    @app_commands.describe(osu_score_id="De osu! score ID om op te zoeken")
    @is_admin()
    async def check_score(self, interaction: discord.Interaction, osu_score_id: str):
        await interaction.response.defer(ephemeral=True)
        try:
            score = await self.db.get_score_by_osu_id(int(osu_score_id))
        except ValueError:
            return await interaction.followup.send("❌ Ongeldig score ID.", ephemeral=True)

        if not score:
            return await interaction.followup.send(f"❌ Score `{osu_score_id}` niet gevonden in de database.", ephemeral=True)

        embed = discord.Embed(title=f"🔍 Score Debug: {osu_score_id}", color=0x8BE9FD)
        embed.add_field(name="osu_score_id", value=f"`{score['osu_score_id']}`", inline=True)
        embed.add_field(name="beatmap_id", value=f"`{score['beatmap_id']}`", inline=True)
        embed.add_field(name="Mods", value=f"`{score['mods']}`", inline=True)
        embed.add_field(name="Score", value=f"`{score['score']:,}`", inline=True)
        embed.add_field(name="Accuracy", value=f"`{score['accuracy']:.2f}%`", inline=True)
        embed.add_field(name="Rank", value=f"`{score['rank']}`", inline=True)
        embed.add_field(name="is_pass", value=f"`{score['is_pass']}`", inline=True)
        embed.add_field(name="is_valid", value=f"`{score['is_valid']}`", inline=True)
        embed.add_field(name="invalid_reason", value=f"`{score['invalid_reason'] or 'N/A'}`", inline=False)
        embed.add_field(name="submitted_at", value=f"`{score['submitted_at']}`", inline=True)
        embed.add_field(name="tracked_at", value=f"`{score['tracked_at']}`", inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="recent_scores_db", description="[Admin] Bekijk de laatste scores in de database")
    @app_commands.describe(limit="Aantal scores (max 20)")
    @is_admin()
    async def recent_scores_db(self, interaction: discord.Interaction, limit: int = 10):
        await interaction.response.defer(ephemeral=True)
        limit = min(limit, 20)
        scores = await self.db.get_all_scores_raw(limit)
        if not scores:
            return await interaction.followup.send("Geen scores in de database.", ephemeral=True)

        lines = []
        for s in scores:
            valid = "✅" if s["is_valid"] else "⚠️"
            passed = "✓" if s["is_pass"] else "✗"
            lines.append(
                f"{valid} **{s['osu_username'] or '?'}** — `{s['score']:,}` | `{s['mods']}` | "
                f"bm:`{s['beatmap_id']}` | {passed} | `{s['tracked_at'].strftime('%H:%M:%S')}`"
            )

        embed = discord.Embed(
            title=f"🗄️ Laatste {limit} scores in DB",
            description="\n".join(lines),
            color=0x6272A4
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="force_poll", description="[Admin] Forceer 1 poll-ronde nu (zonder tracking te starten)")
    @is_admin()
    async def force_poll(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        players = await self.db.get_all_players()
        if not players:
            return await interaction.followup.send("❌ Geen spelers geregistreerd.", ephemeral=True)

        tracking_cog = self.bot.get_cog("TrackingCog")
        if not tracking_cog:
            return await interaction.followup.send("❌ TrackingCog niet geladen.", ephemeral=True)

        await interaction.followup.send(f"🔄 Forceer poll voor {len(players)} speler(s)...", ephemeral=True)
        settings = await self.db.get_guild_settings(interaction.guild.id)
        await tracking_cog._poll_all_players(interaction.guild.id, settings)
        await interaction.followup.send("✅ Force poll klaar. Check het log channel voor details.", ephemeral=True)

    @app_commands.command(name="list_pools", description="Bekijk alle actieve pools in deze server")
    async def list_pools(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        pools = await self.db.get_all_pools(interaction.guild.id)
        if not pools:
            return await interaction.followup.send("Geen pools aangemaakt.", ephemeral=True)

        lines = []
        for p in pools:
            thread = interaction.guild.get_channel_or_thread(p["channel_id"])
            thread_mention = thread.mention if thread else f"*(thread weg, id={p['channel_id']})*"
            maps = await self.db.get_pool_maps(p["id"])
            lines.append(f"**{p['name']}** (ID: `{p['id']}`) — {thread_mention} — {len(maps)} maps")

        embed = discord.Embed(title="🗂️ Actieve Pools", description="\n".join(lines), color=0xBD93F9)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="clear_scores", description="[Admin] ⚠️ Verwijder ALLE scores uit de database (niet ongedaan te maken)")
    @is_admin()
    async def clear_scores(self, interaction: discord.Interaction, confirm: str = ""):
        if confirm != "JA_IK_WEET_HET_ZEKER":
            return await interaction.response.send_message(
                "⚠️ Dit verwijdert **alle scores**. Gebruik:\n`/clear_scores confirm:JA_IK_WEET_HET_ZEKER`",
                ephemeral=True
            )
        await interaction.response.defer(ephemeral=True)
        async with self.db.pool.acquire() as conn:
            await conn.execute("DELETE FROM scores")
        logger.warning(f"Alle scores verwijderd door {interaction.user}")
        await interaction.followup.send("🗑️ Alle scores verwijderd.", ephemeral=True)

async def setup(bot):
    # bot.db en bot.osu worden al geïnitialiseerd in bot.py main()
    await bot.add_cog(AdminCog(bot))
