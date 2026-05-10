import discord
from discord import app_commands
from discord.ext import commands
import re
from checks import admin_check, is_admin


# admin_check() is imported from checks.py


def parse_slot(slot: str) -> str | None:
    slot = slot.upper().strip()
    match = re.match(r"^(NM|HD|HR|DT|FL|EZ|TB)(\d+)$", slot)
    return slot if match else None


def slot_to_category(slot: str) -> str:
    match = re.match(r"^([A-Z]+)\d+$", slot.upper())
    return match.group(1) if match else slot.upper()


async def pool_autocomplete(interaction: discord.Interaction, current: str):
    """Autocomplete voor pool naam — werkt altijd, ook met archived threads."""
    pools = await interaction.client.db.get_all_pools(interaction.guild_id)
    return [
        app_commands.Choice(name=p["name"], value=str(p["id"]))
        for p in pools
        if current.lower() in p["name"].lower()
    ][:25]


MOD_CAT_EMOJI = {"NM": "🔵", "HD": "🟡", "HR": "🔴", "DT": "🟣", "FL": "⚫", "EZ": "🟢", "TB": "🏆"}


async def post_pool_overview(bot, guild: discord.Guild, pool_row):
    """Post of edit het pool overzicht in de thread."""
    thread = guild.get_thread(pool_row["channel_id"])
    if not thread:
        return

    maps = await bot.db.get_pool_maps(pool_row["id"])
    lb_rows = await bot.db.get_pool_leaderboard(pool_row["id"])

    lb_by_map = {}
    for r in lb_rows:
        lb_by_map.setdefault(r["beatmap_id"], []).append(r)

    categories = {}
    for m in maps:
        categories.setdefault(m["mod_category"] or "?", []).append(m)

    embed = discord.Embed(title=f"🎵 {pool_row['name']}", color=0xFF66AA)
    embed.set_footer(text=f"{len(maps)} maps • NF required on all slots")

    for cat in ["NM", "HD", "HR", "DT", "FL", "EZ", "TB", "?"]:
        if cat not in categories:
            continue
        lines = []
        for m in sorted(categories[cat], key=lambda x: x["slot"]):
            map_line = f"`{m['slot']}` **[{m['artist']} - {m['title']} [{m['version']}]](https://osu.ppy.sh/beatmaps/{m['beatmap_id']})**"
            entries = lb_by_map.get(m["beatmap_id"], [])
            if entries:
                top = entries[0]
                miss_str = "FC ✨" if not top["count_miss"] else f"{top['count_miss']}x miss"
                map_line += f"\n  🥇 **{top['osu_username']}** — `{top['score']:,}` • {top['accuracy']:.2f}% • {miss_str}"
            else:
                map_line += "\n  _(no scores yet)_"
            lines.append(map_line)
        embed.add_field(
            name=f"{MOD_CAT_EMOJI.get(cat, '⚪')} {cat}",
            value="\n".join(lines),
            inline=False
        )

    # Probeer bestaand bericht te editen
    existing_msg_id = pool_row.get("leaderboard_message_id")
    if existing_msg_id:
        try:
            msg = await thread.fetch_message(existing_msg_id)
            await msg.edit(embed=embed)
            return
        except (discord.NotFound, discord.HTTPException):
            pass  # Bericht bestaat niet meer, stuur nieuw

    msg = await thread.send(embed=embed)
    await bot.db.save_leaderboard_message_id(pool_row["id"], msg.id)


async def get_pool_by_autocomplete(bot, interaction, pool_id_str: str):
    """Haal pool op via autocomplete value (pool ID string)."""
    try:
        pool_id = int(pool_id_str)
        pool = await bot.db.get_pool_by_id(pool_id)
    except (ValueError, Exception):
        pool = None
    if not pool:
        await interaction.followup.send("❌ Pool not found.")
    return pool


class AdminCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── Speler beheer ────────────────────────────────────────────────────────

    @app_commands.command(name="add_player", description="Voeg een speler handmatig toe")
    @app_commands.describe(member="Discord user", osu_username="osu! username")
    @admin_check()
    async def add_player(self, interaction: discord.Interaction, member: discord.Member, osu_username: str):
        await interaction.response.defer(ephemeral=True)
        user = await self.bot.osu.get_user(osu_username)
        if not user:
            return await interaction.followup.send(f"❌ osu! gebruiker `{osu_username}` not found.")
        await self.bot.db.add_player(
            discord_id=member.id, osu_username=user["username"],
            osu_id=user["id"], added_by=interaction.user.id
        )
        await interaction.followup.send(f"✅ **{user['username']}** (`{user['id']}`) linked to {member.mention}.")

    @app_commands.command(name="remove_player", description="Verwijder een speler")
    @admin_check()
    async def remove_player(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        result = await self.bot.db.remove_player(member.id)
        if result == "DELETE 0":
            return await interaction.followup.send(f"❌ {member.mention} is not in the tracker.")
        await interaction.followup.send(f"✅ {member.mention} removed.")

    @app_commands.command(name="list_players", description="Bekijk alle geregistreerde spelers")
    @admin_check()
    async def list_players(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        players = await self.bot.db.get_all_players()
        if not players:
            return await interaction.followup.send("No players registered.")
        lines = [f"`{i+1}.` **{p['osu_username']}** — <@{p['discord_id']}> (`{p['osu_id']}`)" for i, p in enumerate(players)]
        embed = discord.Embed(title=f"👥 Players ({len(players)})", description="\n".join(lines), color=0xFF66AA)
        await interaction.followup.send(embed=embed)

    # ── Pool beheer ──────────────────────────────────────────────────────────

    @app_commands.command(name="create_pool", description="Maak een nieuwe pool aan (maakt een thread aan)")
    @app_commands.describe(
        name="Pool name (bijv. 'Finals Pool')",
        parent_channel="Channel where the thread will be created (default: current channel)"
    )
    @admin_check()
    async def create_pool(self, interaction: discord.Interaction,
                          name: str,
                          parent_channel: discord.TextChannel = None):
        await interaction.response.defer()

        parent = parent_channel or interaction.channel
        if not isinstance(parent, discord.TextChannel):
            return await interaction.followup.send("❌ Please specify a text channel as parent.")

        pools = await self.bot.db.get_all_pools(interaction.guild_id)
        if any(p["name"].lower() == name.lower() for p in pools):
            return await interaction.followup.send(f"❌ Pool **{name}** already exists.")

        thread = await parent.create_thread(
            name=name,
            type=discord.ChannelType.public_thread,
            reason=f"osu! LAN pool aangemaakt door {interaction.user}"
        )

        pool = await self.bot.db.create_pool(
            name=name, channel_id=thread.id,
            guild_id=interaction.guild_id, created_by=interaction.user.id
        )

        embed = discord.Embed(
            title="✅ Pool created",
            description=f"**{name}** → {thread.mention}\nPool ID: `{pool['id']}`",
            color=0x66FF99
        )
        embed.set_footer(text="Use /add_map to add maps.")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="delete_pool", description="Verwijder een pool uit de database")
    @app_commands.describe(pool="The pool")
    @app_commands.autocomplete(pool=pool_autocomplete)
    @admin_check()
    async def delete_pool(self, interaction: discord.Interaction, pool: str):
        await interaction.response.defer(ephemeral=True)
        pool_row = await get_pool_by_autocomplete(self.bot, interaction, pool)
        if not pool_row:
            return
        await self.bot.db.delete_pool(pool_row["id"])
        await interaction.followup.send(f"✅ Pool **{pool_row['name']}** removed from de database.")

    @app_commands.command(name="add_map", description="Voeg een map toe aan een pool")
    @app_commands.describe(
        pool="The pool",
        beatmap_id="osu! beatmap ID",
        slot="Slot e.g. NM1, HD2, HR3, DT1"
    )
    @app_commands.autocomplete(pool=pool_autocomplete)
    @admin_check()
    async def add_map(self, interaction: discord.Interaction,
                      pool: str,
                      beatmap_id: int,
                      slot: str):
        await interaction.response.defer()

        slot_clean = parse_slot(slot)
        if not slot_clean:
            return await interaction.followup.send("❌ Invalid slot. Use e.g. `NM1`, `HD2`, `HR3`, `DT1`.")

        pool_row = await get_pool_by_autocomplete(self.bot, interaction, pool)
        if not pool_row:
            return

        bm = await self.bot.osu.get_beatmap(beatmap_id)
        if not bm:
            return await interaction.followup.send(f"❌ Beatmap `{beatmap_id}` not found.")

        bms = bm.get("beatmapset", {})
        mod_category = slot_to_category(slot_clean)

        await self.bot.db.add_map_to_pool(
            pool_id=pool_row["id"], beatmap_id=bm["id"], beatmapset_id=bms.get("id"),
            title=bms.get("title", "?"), artist=bms.get("artist", "?"),
            version=bm.get("version", "?"), slot=slot_clean, mod_category=mod_category,
            max_combo=bm.get("max_combo") or 0,
            total_length=bm.get("total_length") or 0
        )

        embed = discord.Embed(
            title=f"✅ Map added — {slot_clean}",
            description=f"**{bms.get('artist')} - {bms.get('title')}** [{bm.get('version')}]",
            color=0x66AAFF, url=f"https://osu.ppy.sh/beatmaps/{bm['id']}"
        )
        embed.add_field(name="Pool", value=pool_row["name"])
        embed.add_field(name="Slot", value=slot_clean)
        embed.add_field(name="Required mods", value=f"NF + {mod_category}" if mod_category != "NM" else "NF only")
        embed.set_thumbnail(url=bms.get("covers", {}).get("list", ""))
        await interaction.followup.send(embed=embed)

        # Post bijgewerkt pool overzicht in thread
        await post_pool_overview(self.bot, interaction.guild, pool_row)

    @app_commands.command(name="remove_map", description="Verwijder een map uit een pool")
    @app_commands.describe(pool="The pool", beatmap_id="osu! beatmap ID")
    @app_commands.autocomplete(pool=pool_autocomplete)
    @admin_check()
    async def remove_map(self, interaction: discord.Interaction, pool: str, beatmap_id: int):
        await interaction.response.defer(ephemeral=True)
        pool_row = await get_pool_by_autocomplete(self.bot, interaction, pool)
        if not pool_row:
            return
        pm = await self.bot.db.get_pool_map(pool_row["id"], beatmap_id)
        if not pm:
            return await interaction.followup.send("❌ Map is not in this pool.")
        await self.bot.db.remove_map_from_pool(pool_row["id"], beatmap_id)
        await interaction.followup.send(f"✅ **{pm['title']}** ({pm['slot']}) removed from **{pool_row['name']}**.")
        await post_pool_overview(self.bot, interaction.guild, pool_row)

    @app_commands.command(name="pool_info", description="Bekijk alle maps in een pool")
    @app_commands.describe(pool="The pool")
    @app_commands.autocomplete(pool=pool_autocomplete)
    async def pool_info(self, interaction: discord.Interaction, pool: str):
        await interaction.response.defer()
        pool_row = await get_pool_by_autocomplete(self.bot, interaction, pool)
        if not pool_row:
            return

        maps = await self.bot.db.get_pool_maps(pool_row["id"])
        if not maps:
            return await interaction.followup.send(f"Pool **{pool_row['name']}** has no maps yet.")

        # Probeer thread mention te maken
        thread = interaction.guild.get_thread(pool_row["channel_id"])
        thread_str = thread.mention if thread else f"Thread `{pool_row['channel_id']}`"

        categories = {}
        for m in maps:
            categories.setdefault(m["mod_category"] or "?", []).append(m)

        cat_emojis = {"NM": "🔵", "HD": "🟡", "HR": "🔴", "DT": "🟣", "FL": "⚫", "EZ": "🟢", "TB": "🏆"}
        embed = discord.Embed(title=f"🎵 {pool_row['name']}", description=thread_str, color=0xFF66AA)
        embed.set_footer(text=f"Pool ID: {pool_row['id']} • {len(maps)} maps total")

        for cat in ["NM", "HD", "HR", "DT", "FL", "EZ", "TB", "?"]:
            if cat not in categories:
                continue
            lines = [
                f"`{m['slot']}` [{m['artist']} - {m['title']} [{m['version']}]"
                f"](https://osu.ppy.sh/beatmaps/{m['beatmap_id']})"
                for m in sorted(categories[cat], key=lambda x: x["slot"])
            ]
            embed.add_field(name=f"{cat_emojis.get(cat, '⚪')} {cat}", value="\n".join(lines), inline=False)

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="list_pools", description="Bekijk alle pools op deze server")
    async def list_pools(self, interaction: discord.Interaction):
        await interaction.response.defer()
        pools = await self.bot.db.get_all_pools(interaction.guild_id)
        if not pools:
            return await interaction.followup.send("No pools created.")

        lines = []
        for p in pools:
            thread = interaction.guild.get_thread(p["channel_id"]) or interaction.guild.get_channel(p["channel_id"])
            thread_str = thread.mention if thread else f"Thread `{p['channel_id']}`"
            lines.append(f"`{p['id']}` **{p['name']}** → {thread_str}")

        embed = discord.Embed(title=f"📋 Pools ({len(pools)})", description="\n".join(lines), color=0x66AAFF)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="set_score_channel", description="Stel het channel in voor score notificaties")
    @app_commands.describe(channel="Channel voor notificaties")
    @admin_check()
    async def set_score_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await self.bot.db.update_guild_settings(interaction.guild_id, score_channel_id=channel.id)
        await interaction.response.send_message(f"✅ Score notifications → {channel.mention}.", ephemeral=True)



    @app_commands.command(name="set_lan_start", description="Stel de LAN starttijd in (bijv. '2026-05-09 10:00')")
    @app_commands.describe(datetime_utc="Datum en tijd in UTC, formaat: YYYY-MM-DD HH:MM")
    @admin_check()
    async def set_lan_start(self, interaction: discord.Interaction, datetime_utc: str):
        await interaction.response.defer(ephemeral=True)
        from datetime import datetime, timezone
        try:
            dt = datetime.strptime(datetime_utc.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        except ValueError:
            return await interaction.followup.send("❌ Ongeldig formaat. Gebruik: `YYYY-MM-DD HH:MM` (UTC)")
        await self.bot.db.update_guild_settings(interaction.guild_id, lan_start_time=dt)
        await interaction.followup.send(f"✅ LAN starttijd ingesteld op **{dt.strftime('%d-%m-%Y %H:%M')} UTC**.")

    @app_commands.command(name="reset_stats", description="Verwijder alle scores en leaderboard data (ONOMKEERBAAR)")
    @app_commands.describe(confirm="Type 'RESET' to confirm")
    @admin_check()
    async def reset_stats(self, interaction: discord.Interaction, confirm: str):
        await interaction.response.defer(ephemeral=True)

        if confirm != "RESET":
            return await interaction.followup.send("❌ Type exactly `RESET` to confirm.")

        async with self.bot.db.pool.acquire() as conn:
            await conn.execute("DELETE FROM pool_leaderboard")
            await conn.execute("DELETE FROM scores")
            await conn.execute("UPDATE pools SET leaderboard_message_id=NULL")
            await conn.execute("UPDATE guild_settings SET lan_stats_message_id=NULL, pool_lb_message_id=NULL, lan_start_time=NULL")

        await interaction.followup.send("✅ All scores, leaderboard data en dashboard message IDs have been removed.")

    @app_commands.command(name="reset_player_scores", description="Verwijder scores van één speler")
    @app_commands.describe(member="The player to reset")
    @admin_check()
    async def reset_player_scores(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)

        player = await self.bot.db.get_player(member.id)
        if not player:
            return await interaction.followup.send("❌ Speler not found.")

        async with self.bot.db.pool.acquire() as conn:
            await conn.execute("DELETE FROM pool_leaderboard WHERE discord_id=$1", member.id)
            await conn.execute("DELETE FROM scores WHERE discord_id=$1", member.id)

        await interaction.followup.send(f"✅ All scores van **{player['osu_username']}** removed.")


async def setup(bot):
    await bot.add_cog(AdminCog(bot))
