import discord
from checks import admin_check
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
        await interaction.followup.send("❌ Pool not found.")
    return pool


class PlayerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="register", description="Koppel your Discord to your osu! account")
    @app_commands.describe(osu_username="Your osu! username")
    async def register(self, interaction: discord.Interaction, osu_username: str):
        await interaction.response.defer(ephemeral=True)
        user = await self.bot.osu.get_user(osu_username)
        if not user:
            return await interaction.followup.send(f"❌ osu! gebruiker `{osu_username}` not found.")
        await self.bot.db.add_player(discord_id=interaction.user.id, osu_username=user["username"], osu_id=user["id"], added_by=interaction.user.id)
        embed = discord.Embed(title="✅ Registered!", description=f"Linked as **{user['username']}**.", color=0x66FF99)
        embed.set_thumbnail(url=user.get("avatar_url", ""))
        rank = user.get("statistics", {}).get("global_rank")
        embed.add_field(name="osu! ID", value=str(user["id"]))
        embed.add_field(name="Rank", value=f"#{rank:,}" if rank else "Unranked")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="link_osu", description="Link an osu! account to the bot for score tracking (admin)")
    @admin_check()
    async def link_osu(self, interaction: discord.Interaction):
        client_id    = os.getenv("OSU_OAUTH_CLIENT_ID") or os.getenv("OSU_CLIENT_ID")
        redirect_uri = os.getenv("OSU_REDIRECT_URI")

        if not redirect_uri:
            return await interaction.response.send_message(
                "❌ OSU_REDIRECT_URI is not set in environment variables.",
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
                status_str = f"\n✅ Bot already linked (token valid until {expires.strftime('%d-%m %H:%M')} UTC)"
            else:
                status_str = "\n⚠️ Existing token expired — relink to refresh."

        embed = discord.Embed(
            title="🔗 Bot OAuth koppelen",
            description=(
                f"Click the link and log in with **your** osu! account.\n"
                f"The bot uses this token for all score lookups.\n\n"
                f"**[→ Link via osu! OAuth]({oauth_url})**"
                f"{status_str}"
            ),
            color=0xFF66AA
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="unregister", description="Verwijder jezelf uit de LAN tracker")
    async def unregister(self, interaction: discord.Interaction):
        result = await self.bot.db.remove_player(interaction.user.id)
        msg = "✅ Removed." if result != "DELETE 0" else "❌ You are not in the tracker."
        await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.command(name="profile", description="Bekijk LAN stats van een speler")
    @app_commands.describe(member="Leave empty for your own profile")
    async def profile(self, interaction: discord.Interaction, member: discord.Member = None):
        await interaction.response.defer()
        target = member or interaction.user
        player = await self.bot.db.get_player(target.id)
        if not player:
            return await interaction.followup.send(f"❌ {'Je bent' if not member else target.display_name + ' is'} not registered.")

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
        embed.add_field(name="Avg. acc", value=fmt_acc(stats["avg_accuracy"]) if stats["avg_accuracy"] else "—", inline=True)
        embed.add_field(name="FCs", value=str(stats["fc_count"] or 0), inline=True)
        embed.add_field(name="Pass rate", value=f"{stats['pass_count']}/{stats['total_scores']}" if stats["total_scores"] else "—", inline=True)

        if pool_summary:
            lines = []
            for ps in pool_summary:
                done, total = ps["maps_done"] or 0, ps["maps_total"] or 0
                pct = int(done / total * 10) if total > 0 else 0
                bar = "█" * pct + "░" * (10 - pct)
                score_str = f" — {fmt_score(ps['total_score'])} pts" if ps["total_score"] else ""
                lines.append(f"**{ps['pool_name']}**: `{bar}` {done}/{total}{score_str}")
            embed.add_field(name="📋 Pool progress", value="\n".join(lines) or "No pool scores", inline=False)

        embed.set_footer(text=f"Discord: {target.display_name}")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="recent", description="Bekijk recente scores")
    @app_commands.describe(member="Leave empty for yourself", limit="Number of scores (max 10)", client="stable, lazer or all")
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
            return await interaction.followup.send(f"❌ {target.display_name} is not registered.")

        scores = await self.bot.db.get_player_all_scores(target.id, limit=limit, client_type=None if client == "all" else client)
        if not scores:
            return await interaction.followup.send(f"No scores found for **{player['osu_username']}**.")

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
    @app_commands.describe(pool="The pool", member="Leave empty for yourself")
    @app_commands.autocomplete(pool=pool_autocomplete)
    async def pool_scores(self, interaction: discord.Interaction, pool: str, member: discord.Member = None):
        await interaction.response.defer()
        target = member or interaction.user
        player = await self.bot.db.get_player(target.id)
        if not player:
            return await interaction.followup.send(f"❌ {target.display_name} is not registered.")

        pool_row = await get_pool(self.bot, interaction, pool)
        if not pool_row:
            return

        scores = await self.bot.db.get_player_pool_scores(target.id, pool_row["id"])
        maps = await self.bot.db.get_pool_maps(pool_row["id"])

        embed = discord.Embed(title=f"🎵 {pool_row['name']} — {player['osu_username']}", color=0xFF66AA)
        embed.set_footer(text=f"{len(scores)}/{len(maps)} maps gespeeld")

        if not scores:
            embed.description = "No valid scores in this pool yet."
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
    @app_commands.describe(member="The player to compare with")
    async def compare(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer()
        player_a = await self.bot.db.get_player(interaction.user.id)
        player_b = await self.bot.db.get_player(member.id)
        if not player_a:
            return await interaction.followup.send("❌ Jij bent not registered.")
        if not player_b:
            return await interaction.followup.send(f"❌ {member.display_name} is not registered.")

        comparisons = await self.bot.db.compare_players(interaction.user.id, member.id, interaction.guild_id)
        if not comparisons:
            return await interaction.followup.send("No pool scores to compare.")

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
        embed.add_field(name="Result", value=f"🔴 **{player_a['osu_username']}**: {wins_a}\n🔵 **{player_b['osu_username']}**: {wins_b}\n⚫ Tie: {ties}")
        if len(lines) > 15:
            embed.set_footer(text=f"Showing 15/{len(lines)} maps")
        await interaction.followup.send(embed=embed)


async def setup(bot):
    await bot.add_cog(PlayerCog(bot))
