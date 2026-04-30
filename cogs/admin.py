import discord
from discord import app_commands
from discord.ext import commands
import re


def admin_only():
    async def predicate(interaction: discord.Interaction):
        return interaction.user.guild_permissions.manage_guild
    return app_commands.check(predicate)


def parse_slot(slot: str) -> str | None:
    """Valideer slot formaat: NM1, HD2, HR3, DT1, etc."""
    slot = slot.upper().strip()
    match = re.match(r"^(NM|HD|HR|DT|FL|EZ|TB)(\d+)$", slot)
    return slot if match else None


def slot_to_category(slot: str) -> str:
    """NM1 -> NM, HD2 -> HD, etc."""
    match = re.match(r"^([A-Z]+)\d+$", slot.upper())
    return match.group(1) if match else slot.upper()


def get_thread_or_channel(guild: discord.Guild, channel_id: int):
    """Haal een thread of channel op — threads staan apart in de cache."""
    return guild.get_thread(channel_id) or guild.get_channel(channel_id)


class AdminCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── Speler beheer ────────────────────────────────────────────────────────

    @app_commands.command(name="add_player", description="Voeg een speler handmatig toe")
    @app_commands.describe(member="Discord gebruiker", osu_username="osu! gebruikersnaam")
    @admin_only()
    async def add_player(self, interaction: discord.Interaction, member: discord.Member, osu_username: str):
        await interaction.response.defer(ephemeral=True)

        user = await self.bot.osu.get_user(osu_username)
        if not user:
            return await interaction.followup.send(f"❌ osu! gebruiker `{osu_username}` niet gevonden.")

        await self.bot.db.add_player(
            discord_id=member.id,
            osu_username=user["username"],
            osu_id=user["id"],
            added_by=interaction.user.id
        )
        await interaction.followup.send(
            f"✅ **{user['username']}** (osu! ID: `{user['id']}`) gekoppeld aan {member.mention}."
        )

    @app_commands.command(name="remove_player", description="Verwijder een speler uit de tracker")
    @admin_only()
    async def remove_player(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        result = await self.bot.db.remove_player(member.id)
        if result == "DELETE 0":
            return await interaction.followup.send(f"❌ {member.mention} staat niet in de tracker.")
        await interaction.followup.send(f"✅ {member.mention} verwijderd.")

    @app_commands.command(name="list_players", description="Bekijk alle geregistreerde spelers")
    @admin_only()
    async def list_players(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        players = await self.bot.db.get_all_players()
        if not players:
            return await interaction.followup.send("Geen spelers geregistreerd.")

        lines = [
            f"`{i+1}.` **{p['osu_username']}** — <@{p['discord_id']}> (osu! ID: `{p['osu_id']}`)"
            for i, p in enumerate(players)
        ]
        embed = discord.Embed(
            title=f"👥 Geregistreerde spelers ({len(players)})",
            description="\n".join(lines),
            color=0xFF66AA
        )
        await interaction.followup.send(embed=embed)

    # ── Pool beheer ──────────────────────────────────────────────────────────
    # Pools zitten in THREADS. De parameter heet `pool_thread` en accepteert
    # een thread ID als integer (gebruiker typt het ID of kopieert het).
    # Reden: discord.py slash command parameters kunnen geen Thread type
    # direct selecteren via de channel picker — we gebruiken een string/int
    # zodat de gebruiker het thread-ID kan invullen.

    @app_commands.command(name="create_pool", description="Registreer een bestaande thread als pool")
    @app_commands.describe(
        thread_id="ID van de thread die als pool dient",
        name="Naam van de pool (bijv. 'Finals Pool')"
    )
    @admin_only()
    async def create_pool(self, interaction: discord.Interaction, thread_id: str, name: str):
        await interaction.response.defer()

        try:
            tid = int(thread_id)
        except ValueError:
            return await interaction.followup.send("❌ Ongeldig thread ID.")

        thread = get_thread_or_channel(interaction.guild, tid)
        if not thread:
            return await interaction.followup.send(
                f"❌ Thread/channel `{tid}` niet gevonden. Is de bot er lid van?"
            )

        # Check of al geregistreerd
        existing = await self.bot.db.get_pool_by_channel(tid)
        if existing:
            return await interaction.followup.send(
                f"❌ Deze thread is al geregistreerd als pool **{existing['name']}**."
            )

        pool = await self.bot.db.create_pool(
            name=name,
            channel_id=tid,
            guild_id=interaction.guild_id,
            created_by=interaction.user.id
        )

        embed = discord.Embed(
            title="✅ Pool aangemaakt",
            description=f"**{name}** → {thread.mention}\nPool ID: `{pool['id']}`",
            color=0x66FF99
        )
        embed.set_footer(text="Gebruik /add_map om maps toe te voegen")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="delete_pool", description="Verwijder een pool (thread blijft bestaan)")
    @app_commands.describe(thread_id="Thread ID van de pool")
    @admin_only()
    async def delete_pool(self, interaction: discord.Interaction, thread_id: str):
        await interaction.response.defer(ephemeral=True)

        try:
            tid = int(thread_id)
        except ValueError:
            return await interaction.followup.send("❌ Ongeldig thread ID.")

        pool = await self.bot.db.get_pool_by_channel(tid)
        if not pool:
            return await interaction.followup.send("❌ Dit thread ID is geen geregistreerde pool.")

        await self.bot.db.delete_pool(pool["id"])
        await interaction.followup.send(
            f"✅ Pool **{pool['name']}** verwijderd uit de database. De thread bestaat nog."
        )

    @app_commands.command(name="add_map", description="Voeg een map toe aan een pool")
    @app_commands.describe(
        thread_id="Thread ID van de pool",
        beatmap_id="osu! beatmap ID",
        slot="Slot bijv. NM1, HD2, HR3, DT1"
    )
    @admin_only()
    async def add_map(self, interaction: discord.Interaction,
                      thread_id: str,
                      beatmap_id: int,
                      slot: str):
        await interaction.response.defer()

        slot_clean = parse_slot(slot)
        if not slot_clean:
            return await interaction.followup.send(
                "❌ Ongeldig slot. Gebruik bijv. `NM1`, `HD2`, `HR3`, `DT1`."
            )

        try:
            tid = int(thread_id)
        except ValueError:
            return await interaction.followup.send("❌ Ongeldig thread ID.")

        pool = await self.bot.db.get_pool_by_channel(tid)
        if not pool:
            return await interaction.followup.send("❌ Dit thread ID is geen geregistreerde pool. Gebruik eerst `/create_pool`.")

        bm = await self.bot.osu.get_beatmap(beatmap_id)
        if not bm:
            return await interaction.followup.send(f"❌ Beatmap `{beatmap_id}` niet gevonden.")

        bms = bm.get("beatmapset", {})
        mod_category = slot_to_category(slot_clean)

        await self.bot.db.add_map_to_pool(
            pool_id=pool["id"],
            beatmap_id=bm["id"],
            beatmapset_id=bms.get("id"),
            title=bms.get("title", "?"),
            artist=bms.get("artist", "?"),
            version=bm.get("version", "?"),
            slot=slot_clean,
            mod_category=mod_category
        )

        thread = get_thread_or_channel(interaction.guild, tid)
        thread_mention = thread.mention if thread else f"thread `{tid}`"

        embed = discord.Embed(
            title=f"✅ Map toegevoegd — {slot_clean}",
            description=f"**{bms.get('artist')} - {bms.get('title')}** [{bm.get('version')}]",
            color=0x66AAFF,
            url=f"https://osu.ppy.sh/beatmaps/{bm['id']}"
        )
        embed.add_field(name="Pool", value=f"{pool['name']} ({thread_mention})")
        embed.add_field(name="Slot", value=slot_clean)
        embed.add_field(name="Vereiste mods", value=f"NF + {mod_category}" if mod_category != "NM" else "NF only")
        embed.set_thumbnail(url=bms.get("covers", {}).get("list", ""))
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="remove_map", description="Verwijder een map uit een pool")
    @app_commands.describe(thread_id="Thread ID van de pool", beatmap_id="osu! beatmap ID")
    @admin_only()
    async def remove_map(self, interaction: discord.Interaction,
                         thread_id: str,
                         beatmap_id: int):
        await interaction.response.defer(ephemeral=True)

        try:
            tid = int(thread_id)
        except ValueError:
            return await interaction.followup.send("❌ Ongeldig thread ID.")

        pool = await self.bot.db.get_pool_by_channel(tid)
        if not pool:
            return await interaction.followup.send("❌ Dit thread ID is geen geregistreerde pool.")

        pm = await self.bot.db.get_pool_map(pool["id"], beatmap_id)
        if not pm:
            return await interaction.followup.send("❌ Map staat niet in deze pool.")

        await self.bot.db.remove_map_from_pool(pool["id"], beatmap_id)
        await interaction.followup.send(
            f"✅ **{pm['title']}** ({pm['slot']}) verwijderd uit **{pool['name']}**."
        )

    @app_commands.command(name="pool_info", description="Bekijk alle maps in een pool")
    @app_commands.describe(thread_id="Thread ID van de pool")
    async def pool_info(self, interaction: discord.Interaction, thread_id: str):
        await interaction.response.defer()

        try:
            tid = int(thread_id)
        except ValueError:
            return await interaction.followup.send("❌ Ongeldig thread ID.")

        pool = await self.bot.db.get_pool_by_channel(tid)
        if not pool:
            return await interaction.followup.send("❌ Dit thread ID is geen geregistreerde pool.")

        maps = await self.bot.db.get_pool_maps(pool["id"])
        thread = get_thread_or_channel(interaction.guild, tid)

        if not maps:
            return await interaction.followup.send(f"Pool **{pool['name']}** heeft nog geen maps.")

        categories = {}
        for m in maps:
            cat = m["mod_category"] or "?"
            categories.setdefault(cat, []).append(m)

        embed = discord.Embed(
            title=f"🎵 {pool['name']}",
            description=thread.mention if thread else f"Thread `{tid}`",
            color=0xFF66AA
        )
        embed.set_footer(text=f"Pool ID: {pool['id']} • {len(maps)} maps totaal")

        cat_emojis = {"NM": "🔵", "HD": "🟡", "HR": "🔴", "DT": "🟣", "FL": "⚫", "EZ": "🟢", "TB": "🏆"}
        for cat in ["NM", "HD", "HR", "DT", "FL", "EZ", "TB", "?"]:
            if cat not in categories:
                continue
            lines = []
            for m in sorted(categories[cat], key=lambda x: x["slot"]):
                lines.append(
                    f"`{m['slot']}` [{m['artist']} - {m['title']} [{m['version']}]"
                    f"](https://osu.ppy.sh/beatmaps/{m['beatmap_id']})"
                )
            embed.add_field(
                name=f"{cat_emojis.get(cat, '⚪')} {cat}",
                value="\n".join(lines),
                inline=False
            )

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="list_pools", description="Bekijk alle pools op deze server")
    async def list_pools(self, interaction: discord.Interaction):
        await interaction.response.defer()

        pools = await self.bot.db.get_all_pools(interaction.guild_id)
        if not pools:
            return await interaction.followup.send("Geen pools aangemaakt.")

        lines = []
        for p in pools:
            thread = get_thread_or_channel(interaction.guild, p["channel_id"])
            thread_str = thread.mention if thread else f"(thread `{p['channel_id']}` niet gevonden)"
            lines.append(f"`{p['id']}` **{p['name']}** → {thread_str}")

        embed = discord.Embed(
            title=f"📋 Pools ({len(pools)})",
            description="\n".join(lines),
            color=0x66AAFF
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="set_score_channel", description="Stel het channel in voor score notificaties")
    @app_commands.describe(channel="Het channel voor notificaties (gewoon een channel, geen thread)")
    @admin_only()
    async def set_score_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await self.bot.db.update_guild_settings(
            interaction.guild_id, score_channel_id=channel.id
        )
        await interaction.response.send_message(
            f"✅ Score notificaties gaan naar {channel.mention}.", ephemeral=True
        )

    @app_commands.command(name="tracking_status", description="Bekijk de huidige tracking status")
    @admin_only()
    async def tracking_status(self, interaction: discord.Interaction):
        settings = await self.bot.db.get_guild_settings(interaction.guild_id)
        players = await self.bot.db.get_all_players()

        status = "🟢 Actief" if settings["tracking_active"] else "🔴 Gestopt"
        embed = discord.Embed(
            title="📡 Tracking Status",
            color=0x66FF99 if settings["tracking_active"] else 0xFF6666
        )
        embed.add_field(name="Status", value=status)
        embed.add_field(name="Spelers getrackt", value=str(len(players)))
        if settings.get("score_channel_id"):
            ch = get_thread_or_channel(interaction.guild, settings["score_channel_id"])
            embed.add_field(name="Score channel", value=ch.mention if ch else "?")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(AdminCog(bot))
