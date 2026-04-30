import discord
from discord import app_commands
from discord.ext import commands
import os

RANK_EMOJIS = {"XH": "🌟", "X": "⭐", "SH": "💿", "S": "💽", "A": "🟢", "B": "🔵", "C": "🟡", "D": "🔴", "F": "💀"}

def rank_emoji(rank): return RANK_EMOJIS.get((rank or "F").upper(), "❓")
def fmt_score(score: int) -> str: return f"{score:,}".replace(",", ".")
def fmt_acc(acc: float) -> str: return f"{acc:.2f}%"
def client_badge(ct: str) -> str: return "🌐 Lazer" if ct == "lazer" else "💾 Stable"

async def pool_autocomplete(interaction: discord.Interaction, current: str):
    pools = await interaction.client.db.get_all_pools(interaction.guild_id)
    return [
        app_commands.Choice(name=p["name"], value=str(p["id"]))
        for p in pools if current.lower() in p["name"].lower()
    ][:25]

async def get_pool(bot, interaction, pool_id_str: str):
    try:
        pool = await bot.db.get_pool_by_id(int(pool_id_str))
    except Exception:
        pool = None
    if not pool:
        await interaction.followup.send("❌ Pool niet gevonden.")
    return pool


class PlayerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="register", description="Koppel je Discord aan je osu! account")
    @app_commands.describe(osu_username="Je osu! gebruikersnaam")
    async def register(self, interaction: discord.Interaction, osu_username: str):
        await interaction.response.defer(ephemeral=True)
        user = await self.bot.osu.get_user(osu_username)
        if not user:
            return await interaction.followup.send(f"❌ osu! gebruiker `{osu_username}` niet gevonden.")
        await self.bot.db.add_player(discord_id=interaction.user.id, osu_username=user["username"], osu_id=user["id"], added_by=interaction.user.id)
        embed = discord.Embed(title="✅ Geregistreerd!", description=f"Gekoppeld als **{user['username']}**.", color=0x66FF99)
        embed.set_thumbnail(url=user.get("avatar_url", ""))
        rank = user.get("statistics", {}).get("global_rank")
        embed.add_field(name="osu! ID", value=str(user["id"]))
        embed.add_field(name="Rank", value=f"#{rank:,}" if rank else "Unranked")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="bot_link", description="Koppel een osu! account aan de bot voor score tracking (admin)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def bot_link(self, interaction: discord.Interaction):
        client_id    = os.getenv("OSU_CLIENT_ID")
        redirect_uri = os.getenv("OSU_REDIRECT_URI")

        if not redirect_uri:
            return await interaction.response.send_message(
                "❌ OSU_REDIRECT_URI is niet ingesteld in de environment variables.",
                ephemeral=True
            )

        import urllib.parse
        params = urllib.parse.urlencode({
            "client_id":     client_id,
            "redirect_uri":  redirect_uri,
            "response_type": "code",
            "scope":         "public identify",
            "state":         "bot",
        })
        oauth_url = f"https://osu.ppy.sh/oauth/authorize?{params}"

        # Check of er al een token is
        existing = await self.bot.db.get_oauth_token(0)
        status_str = ""
        if existing:
            from datetime import datetime, timezone
            expires = existing["expires_at"]
            if hasattr(expires, 'replace'):
                expires = expires.replace(tzinfo=timezone.utc) if expires.tzinfo is None else expires
            if expires > datetime.now(timezone.utc):
                status_str = f"\n✅ Bot is al gekoppeld (token geldig tot {expires.strftime('%d-%m %H:%M')} UTC)"
            else:
                status_str = "\n⚠️ Bestaand token is verlopen — herlink om te vernieuwen."

        embed = discord.Embed(
            title="🔗 Bot OAuth koppelen",
            description=(
                f"Klik op de link en log in met **jouw** osu! account.\n"
                f"De bot gebruikt dit token voor alle score lookups.\n\n"
                f"**[→ Koppel via osu! OAuth]({oauth_url})**"
                f"{status_str}"
            ),
            color=0xFF66AA
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        result = await self.bot.db.remove_player(interaction.user.id)
        msg = "✅ Verwijderd." if result != "DELETE 0" else "❌ Je staat niet in de tracker."
        await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.command(name="profile", description="Bekijk LAN stats van een speler")
    @app_commands.describe(member="Laat leeg voor je eigen profiel")
    async def profile(self, interaction: discord.Interaction, member: discord.Member = None):
        await interaction.response.defer()
        target = member or interaction.user
        player = await self.bot.db.get_player(target.id)
        if not player:
            return await interaction.followup.send(f"❌ {'Je bent' if not member else target.display_name + ' is'} niet geregistreerd.")

        stats = await self.bot.db.get_player_stats_summary(target.id)
        pool_summary = await self.bot.db.get_player_pool_summary(target.id, interaction.guild_id)
        osu_user = await self.bot.osu.get_user_by_id(player["osu_id"])
        global_rank = osu_user.get("statistics", {}).get("global_rank") if osu_user else None

        embed = discord.Embed(title=f"🎮 {player['osu_username']}", url=f"https://osu.ppy.sh/users/{player['osu_id']}", color=0xFF66AA)
        if osu_user:
            embed.set_thumbnail(url=osu_user.get("avatar_url", ""))

        embed.add_field(name="osu! rank", value=f"#{global_rank:,}" if global_rank else "Unranked", inline=True)
        embed.add_field(name="Scores", value=f"🌐 Lazer: **{stats['lazer_scores']}**\n💾 Stable: **{stats['stable_scores']}**", inline=True)
        embed.add_field(name="Pool scores", value=str(stats["pool_scores"] or 0), inline=True)
        embed.add_field(name="Gem. acc", value=fmt_acc(stats["avg_accuracy"]) if stats["avg_accuracy"] else "—", inline=True)
        embed.add_field(name="FC's", value=str(stats["fc_count"] or 0), inline=True)
        embed.add_field(name="Pass rate", value=f"{stats['pass_count']}/{stats['total_scores']}" if stats["total_scores"] else "—", inline=True)

        if pool_summary:
            lines = []
            for ps in pool_summary:
                done, total = ps["maps_done"] or 0, ps["maps_total"] or 0
                pct = int(done / total * 10) if total > 0 else 0
                bar = "█" * pct + "░" * (10 - pct)
                score_str = f" — {fmt_score(ps['total_score'])} pts" if ps["total_score"] else ""
                lines.append(f"**{ps['pool_name']}**: `{bar}` {done}/{total}{score_str}")
            embed.add_field(name="📋 Pool voortgang", value="\n".join(lines) or "Geen pool scores", inline=False)

        embed.set_footer(text=f"Discord: {target.display_name}")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="recent", description="Bekijk recente scores")
    @app_commands.describe(member="Laat leeg voor jezelf", limit="Aantal scores (max 10)", client="stable, lazer of alles")
    @app_commands.choices(client=[
        app_commands.Choice(name="Alles", value="all"),
        app_commands.Choice(name="Lazer", value="lazer"),
        app_commands.Choice(name="Stable", value="stable"),
    ])
    async def recent(self, interaction: discord.Interaction, member: discord.Member = None, limit: int = 5, client: str = "all"):
        await interaction.response.defer()
        target = member or interaction.user
        limit = min(max(limit, 1), 10)
        player = await self.bot.db.get_player(target.id)
        if not player:
            return await interaction.followup.send(f"❌ {target.display_name} is niet geregistreerd.")

        scores = await self.bot.db.get_player_all_scores(target.id, limit=limit, client_type=None if client == "all" else client)
        if not scores:
            return await interaction.followup.send(f"Geen scores gevonden voor **{player['osu_username']}**.")

        embed = discord.Embed(title=f"🕐 Recent — {player['osu_username']}", color=0x88AAFF)
        for s in scores:
            title = s.get("title") or f"Beatmap {s['beatmap_id']}"
            flags = ("" if s["is_valid"] else " ⚠️") + (" 🎱" if s["is_pool_score"] else "")
            embed.add_field(
                name=f"{rank_emoji(s['rank'])} {title}{' `'+s['slot']+'`' if s.get('slot') else ''}{flags}",
                value=(
                    f"{client_badge(s['client_type'])} • `{s['mods'] or 'NM'}` • "
                    f"**{fmt_score(s['score'])}** • {fmt_acc(s['accuracy'])} • {s['count_miss']}x miss"
                    + (f"\n⚠️ _{s['invalid_reason']}_" if not s["is_valid"] and s.get("invalid_reason") else "")
                ),
                inline=False
            )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="pool_scores", description="Jouw scores in een specifieke pool")
    @app_commands.describe(pool="De pool", member="Laat leeg voor jezelf")
    @app_commands.autocomplete(pool=pool_autocomplete)
    async def pool_scores(self, interaction: discord.Interaction, pool: str, member: discord.Member = None):
        await interaction.response.defer()
        target = member or interaction.user
        player = await self.bot.db.get_player(target.id)
        if not player:
            return await interaction.followup.send(f"❌ {target.display_name} is niet geregistreerd.")

        pool_row = await get_pool(self.bot, interaction, pool)
        if not pool_row:
            return

        scores = await self.bot.db.get_player_pool_scores(target.id, pool_row["id"])
        maps = await self.bot.db.get_pool_maps(pool_row["id"])

        embed = discord.Embed(title=f"🎵 {pool_row['name']} — {player['osu_username']}", color=0xFF66AA)
        embed.set_footer(text=f"{len(scores)}/{len(maps)} maps gespeeld")

        if not scores:
            embed.description = "Nog geen geldige scores in deze pool."
        else:
            by_cat: dict = {}
            for s in scores:
                by_cat.setdefault(s["mod_category"] or "?", []).append(s)
            for cat in ["NM", "HD", "HR", "DT", "FL", "EZ", "TB", "?"]:
                if cat not in by_cat:
                    continue
                lines = [
                    f"`{s['slot']}` {rank_emoji(s['rank'])} **{fmt_score(s['score'])}** • "
                    f"{fmt_acc(s['accuracy'])} • {s['count_miss']}x miss • `{s['mods']}`"
                    for s in sorted(by_cat[cat], key=lambda x: x["slot"])
                ]
                embed.add_field(name=cat, value="\n".join(lines), inline=False)

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="compare", description="Vergelijk pool scores met iemand anders")
    @app_commands.describe(member="De speler om mee te vergelijken")
    async def compare(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer()
        player_a = await self.bot.db.get_player(interaction.user.id)
        player_b = await self.bot.db.get_player(member.id)
        if not player_a:
            return await interaction.followup.send("❌ Jij bent niet geregistreerd.")
        if not player_b:
            return await interaction.followup.send(f"❌ {member.display_name} is niet geregistreerd.")

        comparisons = await self.bot.db.compare_players(interaction.user.id, member.id, interaction.guild_id)
        if not comparisons:
            return await interaction.followup.send("Geen pool scores om te vergelijken.")

        wins_a = wins_b = ties = 0
        lines = []
        for row in comparisons:
            sa, sb, aa, ab = row["score_a"], row["score_b"], row["acc_a"], row["acc_b"]
            if sa and sb:
                if sa > sb: wins_a += 1; indicator = "🔴"
                elif sb > sa: wins_b += 1; indicator = "🔵"
                else: ties += 1; indicator = "⚫"
            elif sa: wins_a += 1; indicator = "🔴"
            elif sb: wins_b += 1; indicator = "🔵"
            else: indicator = "⬜"
            lines.append(
                f"{indicator} `{row['slot']}` **{(row['title'] or '?')[:25]}**\n"
                f"  🔴 {fmt_score(sa) if sa else '—'} ({fmt_acc(aa) if aa else '—'}) "
                f"vs 🔵 {fmt_score(sb) if sb else '—'} ({fmt_acc(ab) if ab else '—'})"
            )

        embed = discord.Embed(
            title=f"⚔️ {player_a['osu_username']} vs {player_b['osu_username']}",
            description="\n".join(lines[:15]), color=0xFFAA33
        )
        embed.add_field(name="Resultaat", value=f"🔴 **{player_a['osu_username']}**: {wins_a}\n🔵 **{player_b['osu_username']}**: {wins_b}\n⚫ Gelijk: {ties}")
        if len(lines) > 15:
            embed.set_footer(text=f"Toont 15/{len(lines)} maps")
        await interaction.followup.send(embed=embed)


async def setup(bot):
    await bot.add_cog(PlayerCog(bot))
