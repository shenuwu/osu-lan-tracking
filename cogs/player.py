import discord
from discord import app_commands
from discord.ext import commands


RANK_EMOJIS = {
    "XH": "🌟", "X": "⭐", "SH": "💿", "S": "💽",
    "A": "🟢", "B": "🔵", "C": "🟡", "D": "🔴", "F": "💀"
}


def rank_emoji(rank: str) -> str:
    return RANK_EMOJIS.get((rank or "F").upper(), "❓")


def format_score(score: int) -> str:
    return f"{score:,}".replace(",", ".")


def format_acc(acc: float) -> str:
    return f"{acc:.2f}%"


def client_badge(client_type: str) -> str:
    return "🌐 Lazer" if client_type == "lazer" else "💾 Stable"


def get_thread(guild: discord.Guild, tid: int):
    return guild.get_thread(tid) or guild.get_channel(tid)


async def resolve_pool(bot, interaction: discord.Interaction, thread_id: str):
    try:
        tid = int(thread_id)
    except ValueError:
        await interaction.followup.send("❌ Ongeldig thread ID.")
        return None, None
    pool = await bot.db.get_pool_by_channel(tid)
    if not pool:
        await interaction.followup.send("❌ Dit thread ID is geen geregistreerde pool.")
        return None, None
    return tid, pool


class PlayerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── Registratie ──────────────────────────────────────────────────────────

    @app_commands.command(name="register", description="Koppel je Discord aan je osu! account")
    @app_commands.describe(osu_username="Je osu! gebruikersnaam")
    async def register(self, interaction: discord.Interaction, osu_username: str):
        await interaction.response.defer(ephemeral=True)

        user = await self.bot.osu.get_user(osu_username)
        if not user:
            return await interaction.followup.send(f"❌ osu! gebruiker `{osu_username}` niet gevonden.")

        await self.bot.db.add_player(
            discord_id=interaction.user.id,
            osu_username=user["username"],
            osu_id=user["id"],
            added_by=interaction.user.id
        )
        embed = discord.Embed(
            title="✅ Geregistreerd!",
            description=f"Je bent nu gekoppeld als **{user['username']}**.",
            color=0x66FF99
        )
        embed.set_thumbnail(url=user.get("avatar_url", ""))
        embed.add_field(name="osu! ID", value=str(user["id"]))
        rank = user.get("statistics", {}).get("global_rank")
        embed.add_field(name="Rank", value=f"#{rank:,}" if rank else "Unranked")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="unregister", description="Verwijder jezelf uit de LAN tracker")
    async def unregister(self, interaction: discord.Interaction):
        result = await self.bot.db.remove_player(interaction.user.id)
        if result == "DELETE 0":
            return await interaction.response.send_message("❌ Je staat niet in de tracker.", ephemeral=True)
        await interaction.response.send_message("✅ Je bent verwijderd uit de tracker.", ephemeral=True)

    # ── Profiel ──────────────────────────────────────────────────────────────

    @app_commands.command(name="profile", description="Bekijk LAN stats van een speler")
    @app_commands.describe(member="Laat leeg voor je eigen profiel")
    async def profile(self, interaction: discord.Interaction, member: discord.Member = None):
        await interaction.response.defer()
        target = member or interaction.user

        player = await self.bot.db.get_player(target.id)
        if not player:
            name = "Je bent" if not member else f"{target.display_name} is"
            return await interaction.followup.send(f"❌ {name} niet geregistreerd. Gebruik `/register`.")

        stats = await self.bot.db.get_player_stats_summary(target.id)
        pool_summary = await self.bot.db.get_player_pool_summary(target.id, interaction.guild_id)
        osu_user = await self.bot.osu.get_user_by_id(player["osu_id"])
        global_rank = osu_user.get("statistics", {}).get("global_rank") if osu_user else None

        embed = discord.Embed(
            title=f"🎮 {player['osu_username']}",
            url=f"https://osu.ppy.sh/users/{player['osu_id']}",
            color=0xFF66AA
        )
        if osu_user:
            embed.set_thumbnail(url=osu_user.get("avatar_url", ""))

        embed.add_field(name="osu! rank", value=f"#{global_rank:,}" if global_rank else "Unranked", inline=True)
        embed.add_field(
            name="Scores bijgehouden",
            value=f"🌐 Lazer: **{stats['lazer_scores']}**\n💾 Stable: **{stats['stable_scores']}**",
            inline=True
        )
        embed.add_field(name="Pool scores", value=str(stats["pool_scores"] or 0), inline=True)
        embed.add_field(name="Gem. accuracy", value=format_acc(stats["avg_accuracy"]) if stats["avg_accuracy"] else "—", inline=True)
        embed.add_field(name="FC's", value=str(stats["fc_count"] or 0), inline=True)
        embed.add_field(name="Pass rate", value=f"{stats['pass_count']}/{stats['total_scores']}" if stats["total_scores"] else "—", inline=True)

        if pool_summary:
            pool_lines = []
            for ps in pool_summary:
                done = ps["maps_done"] or 0
                total = ps["maps_total"] or 0
                pct = int((done / total * 10)) if total > 0 else 0
                bar = "█" * pct + "░" * (10 - pct)
                score_str = f" — {format_score(ps['total_score'])} pts" if ps["total_score"] else ""
                pool_lines.append(f"**{ps['pool_name']}**: `{bar}` {done}/{total}{score_str}")
            embed.add_field(name="📋 Pool voortgang", value="\n".join(pool_lines) or "Geen pool scores", inline=False)

        embed.set_footer(text=f"Discord: {target.display_name}")
        await interaction.followup.send(embed=embed)

    # ── Recent scores ────────────────────────────────────────────────────────

    @app_commands.command(name="recent", description="Bekijk recente scores")
    @app_commands.describe(member="Laat leeg voor jezelf", limit="Aantal scores (max 10)", client="Alleen stable, lazer, of alles")
    @app_commands.choices(client=[
        app_commands.Choice(name="Alles",  value="all"),
        app_commands.Choice(name="Lazer",  value="lazer"),
        app_commands.Choice(name="Stable", value="stable"),
    ])
    async def recent(self, interaction: discord.Interaction,
                     member: discord.Member = None,
                     limit: int = 5,
                     client: str = "all"):
        await interaction.response.defer()
        target = member or interaction.user
        limit = min(max(limit, 1), 10)

        player = await self.bot.db.get_player(target.id)
        if not player:
            return await interaction.followup.send(f"❌ {target.display_name} is niet geregistreerd.")

        client_filter = None if client == "all" else client
        scores = await self.bot.db.get_player_all_scores(target.id, limit=limit, client_type=client_filter)

        if not scores:
            return await interaction.followup.send(f"Geen scores gevonden voor **{player['osu_username']}**.")

        embed = discord.Embed(title=f"🕐 Recente scores — {player['osu_username']}", color=0x88AAFF)

        for s in scores:
            title = s.get("title") or f"Beatmap {s['beatmap_id']}"
            slot_info  = f" `{s['slot']}`" if s.get("slot") else ""
            nf_badge   = " `NF`" if s["has_nf"] else ""
            pool_badge = " 🎱" if s["is_pool_score"] else ""
            valid_badge = "" if s["is_valid"] else " ⚠️"

            embed.add_field(
                name=f"{rank_emoji(s['rank'])} {title}{slot_info}{pool_badge}{valid_badge}",
                value=(
                    f"{client_badge(s['client_type'])}{nf_badge} • "
                    f"`{s['mods'] or 'NM'}` • "
                    f"**{format_score(s['score'])}** • "
                    f"{format_acc(s['accuracy'])} • "
                    f"{s['count_miss']}x miss"
                    + (f"\n⚠️ _{s['invalid_reason']}_" if not s["is_valid"] and s.get("invalid_reason") else "")
                ),
                inline=False
            )

        await interaction.followup.send(embed=embed)

    # ── Pool scores ──────────────────────────────────────────────────────────

    @app_commands.command(name="pool_scores", description="Jouw scores in een specifieke pool")
    @app_commands.describe(thread_id="Thread ID van de pool", member="Laat leeg voor jezelf")
    async def pool_scores(self, interaction: discord.Interaction,
                          thread_id: str,
                          member: discord.Member = None):
        await interaction.response.defer()
        target = member or interaction.user

        player = await self.bot.db.get_player(target.id)
        if not player:
            return await interaction.followup.send(f"❌ {target.display_name} is niet geregistreerd.")

        tid, pool = await resolve_pool(self.bot, interaction, thread_id)
        if not pool:
            return

        scores = await self.bot.db.get_player_pool_scores(target.id, pool["id"])
        maps = await self.bot.db.get_pool_maps(pool["id"])

        embed = discord.Embed(
            title=f"🎵 {pool['name']} — {player['osu_username']}",
            color=0xFF66AA
        )
        embed.set_footer(text=f"{len(scores)}/{len(maps)} maps gespeeld")

        if not scores:
            embed.description = "Nog geen geldige scores in deze pool."
        else:
            by_cat: dict = {}
            for s in scores:
                cat = s["mod_category"] or "?"
                by_cat.setdefault(cat, []).append(s)

            for cat in ["NM", "HD", "HR", "DT", "FL", "EZ", "TB", "?"]:
                if cat not in by_cat:
                    continue
                lines = []
                for s in sorted(by_cat[cat], key=lambda x: x["slot"]):
                    lines.append(
                        f"`{s['slot']}` {rank_emoji(s['rank'])} "
                        f"**{format_score(s['score'])}** • {format_acc(s['accuracy'])} • "
                        f"{s['count_miss']}x miss • `{s['mods']}`"
                    )
                embed.add_field(name=cat, value="\n".join(lines), inline=False)

        await interaction.followup.send(embed=embed)

    # ── Compare ──────────────────────────────────────────────────────────────

    @app_commands.command(name="compare", description="Vergelijk jouw pool scores met iemand anders")
    @app_commands.describe(member="De speler om mee te vergelijken")
    async def compare(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer()

        player_a = await self.bot.db.get_player(interaction.user.id)
        player_b = await self.bot.db.get_player(member.id)

        if not player_a:
            return await interaction.followup.send("❌ Jij bent niet geregistreerd.")
        if not player_b:
            return await interaction.followup.send(f"❌ {member.display_name} is niet geregistreerd.")

        comparisons = await self.bot.db.compare_players(
            interaction.user.id, member.id, interaction.guild_id
        )
        if not comparisons:
            return await interaction.followup.send("Geen pool scores gevonden om te vergelijken.")

        wins_a = wins_b = ties = 0
        lines = []
        for row in comparisons:
            sa, sb = row["score_a"], row["score_b"]
            aa, ab = row["acc_a"], row["acc_b"]

            if sa and sb:
                indicator = "🔴" if sa > sb else "🔵" if sb > sa else "⚫"
                if sa > sb: wins_a += 1
                elif sb > sa: wins_b += 1
                else: ties += 1
            elif sa:
                indicator = "🔴"; wins_a += 1
            elif sb:
                indicator = "🔵"; wins_b += 1
            else:
                indicator = "⬜"

            title_short = (row["title"] or "?")[:25]
            sa_str = format_score(sa) if sa else "—"
            sb_str = format_score(sb) if sb else "—"
            aa_str = format_acc(aa) if aa else "—"
            ab_str = format_acc(ab) if ab else "—"

            lines.append(
                f"{indicator} `{row['slot']}` **{title_short}**\n"
                f"  🔴 {sa_str} ({aa_str}) vs 🔵 {sb_str} ({ab_str})"
            )

        embed = discord.Embed(
            title=f"⚔️ {player_a['osu_username']} vs {player_b['osu_username']}",
            description="\n".join(lines[:15]),
            color=0xFFAA33
        )
        embed.add_field(
            name="Resultaat",
            value=(
                f"🔴 **{player_a['osu_username']}**: {wins_a} wins\n"
                f"🔵 **{player_b['osu_username']}**: {wins_b} wins\n"
                f"⚫ Gelijk: {ties}"
            )
        )
        if len(lines) > 15:
            embed.set_footer(text=f"Toont 15/{len(lines)} maps")

        await interaction.followup.send(embed=embed)


async def setup(bot):
    await bot.add_cog(PlayerCog(bot))
