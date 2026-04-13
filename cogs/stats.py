import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime


RANK_MEDALS = ["🥇", "🥈", "🥉"]


class StatsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @property
    def db(self):
        return self.bot.db

    @app_commands.command(name="leaderboard", description="Bekijk het algemene LAN leaderboard op totale score")
    async def leaderboard(self, interaction: discord.Interaction):
        await interaction.response.defer()
        rows = await self.db.get_global_leaderboard(interaction.guild.id)
        if not rows:
            return await interaction.followup.send("Nog geen scores gevonden. Start tracking met `/start_tracking`.")

        lines = []
        for i, r in enumerate(rows[:15]):
            medal = RANK_MEDALS[i] if i < 3 else f"`#{i+1}`"
            total = r["total_score"] or 0
            acc = r["avg_accuracy"] or 0
            maps = r["maps_played"] or 0
            lines.append(f"{medal} **{r['osu_username']}** — `{total:,}` pts | {acc:.2f}% | {maps} maps")

        embed = discord.Embed(
            title="🏆 LAN Leaderboard — Totale Score",
            description="\n".join(lines),
            color=0xFFD700
        )
        embed.set_footer(text=f"Bijgewerkt: {datetime.now().strftime('%d-%m-%Y %H:%M')}")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="rankings", description="Bekijk rankings op een specifieke stat")
    @app_commands.describe(stat="Welke stat wil je ranken?")
    @app_commands.choices(stat=[
        app_commands.Choice(name="Accuracy", value="accuracy"),
        app_commands.Choice(name="Maps gespeeld", value="maps"),
        app_commands.Choice(name="Totale score", value="total"),
        app_commands.Choice(name="FC count", value="fc"),
        app_commands.Choice(name="S-ranks", value="srank"),
        app_commands.Choice(name="Top score", value="top"),
    ])
    async def rankings(self, interaction: discord.Interaction, stat: str):
        await interaction.response.defer()
        rows = await self.db.get_global_leaderboard(interaction.guild.id)
        if not rows:
            return await interaction.followup.send("Nog geen scores gevonden.")

        stat_map = {
            "accuracy": ("avg_accuracy", "Gem. Accuracy", lambda v: f"{v:.2f}%" if v else "0%"),
            "maps": ("maps_played", "Maps Gespeeld", lambda v: str(v or 0)),
            "total": ("total_score", "Totale Score", lambda v: f"{v:,}" if v else "0"),
            "fc": ("fc_count", "FC Count", lambda v: str(v or 0)),
            "srank": ("s_ranks", "S-Ranks", lambda v: str(v or 0)),
            "top": ("top_score", "Top Score", lambda v: f"{v:,}" if v else "0"),
        }
        key, label, fmt = stat_map[stat]
        sorted_rows = sorted(rows, key=lambda r: (r[key] or 0), reverse=True)

        lines = []
        for i, r in enumerate(sorted_rows[:15]):
            medal = RANK_MEDALS[i] if i < 3 else f"`#{i+1}`"
            val = fmt(r[key])
            lines.append(f"{medal} **{r['osu_username']}** — {val}")

        embed = discord.Embed(
            title=f"📊 Rankings — {label}",
            description="\n".join(lines),
            color=0x8BE9FD
        )
        embed.set_footer(text=f"Bijgewerkt: {datetime.now().strftime('%d-%m-%Y %H:%M')}")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="compare", description="Vergelijk je LAN stats met een andere speler")
    @app_commands.describe(other="De andere Discord member om mee te vergelijken")
    async def compare(self, interaction: discord.Interaction, other: discord.Member):
        await interaction.response.defer()

        p1 = await self.db.get_player(interaction.user.id)
        p2 = await self.db.get_player(other.id)

        if not p1:
            return await interaction.followup.send("❌ Jij bent niet geregistreerd. Gebruik `/register` eerst.")
        if not p2:
            return await interaction.followup.send(f"❌ **{other.display_name}** is niet geregistreerd.")

        s1 = await self.db.get_player_stats(interaction.user.id)
        s2 = await self.db.get_player_stats(other.id)

        def calc_stats(scores):
            passed = [s for s in scores if s["is_pass"]]
            return {
                "maps": len(scores),
                "passes": len(passed),
                "avg_acc": sum(s["accuracy"] for s in passed) / len(passed) if passed else 0,
                "top_score": max((s["score"] for s in passed), default=0),
                "total_score": sum(s["score"] for s in scores),
                "fc": sum(1 for s in passed if s["count_miss"] == 0),
                "s_ranks": sum(1 for s in passed if s["rank"] in ("S", "SS", "X", "XH", "SH")),
                "avg_combo": sum(s["max_combo"] for s in passed) / len(passed) if passed else 0,
            }

        st1 = calc_stats(s1)
        st2 = calc_stats(s2)

        def cmp(v1, v2, higher_better=True):
            if higher_better:
                return "🟢" if v1 > v2 else ("🔴" if v1 < v2 else "⚪")
            else:
                return "🟢" if v1 < v2 else ("🔴" if v1 > v2 else "⚪")

        rows = [
            ("Maps gespeeld", st1["maps"], st2["maps"], str, True),
            ("Passes", st1["passes"], st2["passes"], str, True),
            ("Gem. Accuracy", st1["avg_acc"], st2["avg_acc"], lambda v: f"{v:.2f}%", True),
            ("Top Score", st1["top_score"], st2["top_score"], lambda v: f"{v:,}", True),
            ("Totale Score", st1["total_score"], st2["total_score"], lambda v: f"{v:,}", True),
            ("FC's", st1["fc"], st2["fc"], str, True),
            ("S-Ranks", st1["s_ranks"], st2["s_ranks"], str, True),
            ("Gem. Combo", st1["avg_combo"], st2["avg_combo"], lambda v: f"{v:.0f}x", True),
        ]

        col1_lines, stat_lines, col2_lines = [], [], []
        for (label, v1, v2, fmt, hb) in rows:
            stat_lines.append(label)
            col1_lines.append(f"{cmp(v1, v2, hb)} {fmt(v1)}")
            col2_lines.append(f"{fmt(v2)} {cmp(v2, v1, hb)}")

        embed = discord.Embed(
            title=f"⚔️ {p1['osu_username']} vs {p2['osu_username']}",
            color=0xFF79C6
        )
        embed.add_field(name=p1["osu_username"], value="\n".join(col1_lines), inline=True)
        embed.add_field(name="Stat", value="\n".join(stat_lines), inline=True)
        embed.add_field(name=p2["osu_username"], value="\n".join(col2_lines), inline=True)
        embed.set_footer(text="🟢 = beter | 🔴 = slechter | ⚪ = gelijk")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="recent", description="Bekijk de recentste scores van een speler op deze LAN")
    @app_commands.describe(member="Discord member (leeg = jezelf)", limit="Aantal scores (max 10)")
    async def recent(self, interaction: discord.Interaction, member: discord.Member = None, limit: int = 5):
        await interaction.response.defer()
        target = member or interaction.user
        player = await self.db.get_player(target.id)
        if not player:
            return await interaction.followup.send(f"❌ **{target.display_name}** is niet geregistreerd.")

        limit = min(limit, 10)
        scores = await self.db.get_player_stats(target.id)
        scores = scores[:limit]

        if not scores:
            return await interaction.followup.send(f"Geen scores gevonden voor **{player['osu_username']}**.")

        rank_emojis = {"SS": "💛", "X": "💛", "XH": "🩶", "SH": "🩶", "S": "🌟", "A": "💚", "B": "🔵", "C": "🟠", "D": "🔴", "F": "⬛"}
        lines = []
        for s in scores:
            emoji = rank_emojis.get(s["rank"], "⬜")
            mods = f"+{s['mods']}" if s["mods"] != "NM" else ""
            time_str = s["submitted_at"].strftime("%H:%M")
            lines.append(
                f"{emoji} `{s['score']:,}` | **{s['accuracy']:.2f}%** | {s['max_combo']}x | {mods} | {s['count_miss']}❌ | {time_str}"
            )

        embed = discord.Embed(
            title=f"🕐 Recente scores — {player['osu_username']}",
            description="\n".join(lines),
            color=0x6272A4
        )
        embed.set_thumbnail(url=f"https://s.ppy.sh/a/{player['osu_id']}")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="pool_scores", description="Bekijk je eigen scores op een specifieke pool")
    @app_commands.describe(pool_channel="Het pool channel")
    async def pool_scores(self, interaction: discord.Interaction, pool_channel: discord.TextChannel):
        await interaction.response.defer()
        player = await self.db.get_player(interaction.user.id)
        if not player:
            return await interaction.followup.send("❌ Je bent niet geregistreerd.")

        pool = await self.db.get_pool_by_channel(pool_channel.id)
        if not pool:
            return await interaction.followup.send("❌ Dat is geen geregistreerde pool.")

        maps = await self.db.get_pool_maps(pool["id"])
        map_ids = {m["beatmap_id"]: m for m in maps}
        all_scores = await self.db.get_player_stats(interaction.user.id)
        pool_scores = [s for s in all_scores if s["beatmap_id"] in map_ids and s["is_pass"]]

        if not pool_scores:
            return await interaction.followup.send(f"Geen scores gevonden in pool **{pool['name']}**.")

        lines = []
        for s in pool_scores:
            m = map_ids[s["beatmap_id"]]
            mods = f"+{s['mods']}" if s["mods"] != "NM" else ""
            lines.append(f"**{m['slot']}** `{s['score']:,}` | {s['accuracy']:.2f}% | {s['max_combo']}x {mods}")

        embed = discord.Embed(
            title=f"🎯 Jouw scores in pool {pool['name']}",
            description="\n".join(lines),
            color=0xBD93F9
        )
        await interaction.followup.send(embed=embed)


async def setup(bot):
    await bot.add_cog(StatsCog(bot))
