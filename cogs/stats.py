import discord
from discord import app_commands
from discord.ext import commands
from collections import defaultdict


RANK_EMOJIS = {
    "XH": "🌟", "X": "⭐", "SH": "💿", "S": "💽",
    "A": "🟢", "B": "🔵", "C": "🟡", "D": "🔴", "F": "💀"
}

MEDALS = ["🥇", "🥈", "🥉"]

MOD_CAT_EMOJI = {
    "NM": "🔵", "HD": "🟡", "HR": "🔴", "DT": "🟣",
    "FL": "⚫", "EZ": "🟢", "TB": "🏆",
}


def format_score(score) -> str:
    return f"{int(score):,}".replace(",", ".") if score else "—"


def format_acc(acc) -> str:
    return f"{acc:.2f}%" if acc is not None else "—"


def medal(i: int) -> str:
    return MEDALS[i] if i < len(MEDALS) else f"`#{i+1}`"


class StatsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── Pool leaderboard (live, per map) ────────────────────────────────────

    @app_commands.command(name="leaderboard", description="Pool leaderboard — beste scores per map")
    @app_commands.describe(
        pool_channel="Het pool channel",
        category="Filter op mod categorie (bijv. NM, HD, HR, DT)"
    )
    async def leaderboard(self, interaction: discord.Interaction,
                          pool_channel: discord.TextChannel,
                          category: str = None):
        await interaction.response.defer()

        pool = await self.bot.db.get_pool_by_channel(pool_channel.id)
        if not pool:
            return await interaction.followup.send("❌ Dit channel is geen pool.")

        rows = await self.bot.db.get_pool_leaderboard(pool["id"])
        if not rows:
            return await interaction.followup.send(f"Nog geen scores in **{pool['name']}**.")

        # Groepeer op beatmap
        maps: dict[int, list] = defaultdict(list)
        map_meta: dict[int, dict] = {}
        for r in rows:
            if category and r["mod_category"].upper() != category.upper():
                continue
            maps[r["beatmap_id"]].append(r)
            if r["beatmap_id"] not in map_meta:
                map_meta[r["beatmap_id"]] = {
                    "title": r["title"],
                    "artist": r["artist"],
                    "version": r["version"],
                    "slot": r["slot"],
                    "mod_category": r["mod_category"],
                }

        if not maps:
            return await interaction.followup.send("Geen scores gevonden voor dit filter.")

        # Sorteer maps op slot
        sorted_maps = sorted(map_meta.items(), key=lambda x: (x[1]["mod_category"], x[1]["slot"]))

        # Stuur per map of bundel in embed fields
        embed = discord.Embed(
            title=f"🏆 {pool['name']}{'  —  ' + category.upper() if category else ''}",
            color=0xFF66AA
        )
        embed.set_footer(text="Lazer NF scores • beste score per speler per map")

        for beatmap_id, meta in sorted_maps:
            entries = maps[beatmap_id]
            cat_emoji = MOD_CAT_EMOJI.get(meta["mod_category"], "⚪")
            title_short = meta["title"][:30] + "…" if len(meta["title"]) > 30 else meta["title"]

            lines = []
            for i, e in enumerate(entries):  # al gesorteerd DESC
                miss_str = f"{e['count_miss']}x miss" if e["count_miss"] else "FC ✨"
                lines.append(
                    f"{medal(i)} **{e['osu_username']}** — "
                    f"`{format_score(e['score'])}` • {format_acc(e['accuracy'])} • "
                    f"{miss_str} • `{e['mods']}`"
                )

            embed.add_field(
                name=f"{cat_emoji} `{meta['slot']}` {title_short} [{meta['version']}]",
                value="\n".join(lines) if lines else "—",
                inline=False
            )

            # Discord embed limit: max 25 fields
            if len(embed.fields) >= 24:
                embed.set_footer(text="⚠️ Te veel maps voor 1 embed — gebruik /leaderboard met category filter")
                break

        await interaction.followup.send(embed=embed)

    # ── Map leaderboard (1 specifieke map) ──────────────────────────────────

    @app_commands.command(name="map_leaderboard", description="Leaderboard van 1 specifieke map in een pool")
    @app_commands.describe(pool_channel="Het pool channel", slot="Slot bijv. NM1, HD2")
    async def map_leaderboard(self, interaction: discord.Interaction,
                               pool_channel: discord.TextChannel,
                               slot: str):
        await interaction.response.defer()

        pool = await self.bot.db.get_pool_by_channel(pool_channel.id)
        if not pool:
            return await interaction.followup.send("❌ Dit channel is geen pool.")

        maps = await self.bot.db.get_pool_maps(pool["id"])
        target_map = next((m for m in maps if m["slot"].upper() == slot.upper()), None)
        if not target_map:
            return await interaction.followup.send(f"❌ Slot `{slot}` niet gevonden in deze pool.")

        entries = await self.bot.db.get_map_leaderboard(pool["id"], target_map["beatmap_id"])
        
        cat_emoji = MOD_CAT_EMOJI.get(target_map["mod_category"], "⚪")
        embed = discord.Embed(
            title=f"{cat_emoji} `{slot}` — {target_map['artist']} - {target_map['title']} [{target_map['version']}]",
            url=f"https://osu.ppy.sh/beatmaps/{target_map['beatmap_id']}",
            color=0x66AAFF
        )

        if not entries:
            embed.description = "Nog geen scores op deze map."
        else:
            lines = []
            for i, e in enumerate(entries):
                miss_str = f"{e['count_miss']}x miss" if e["count_miss"] else "FC ✨"
                rank_em = RANK_EMOJIS.get(e["rank"], "❓")
                lines.append(
                    f"{medal(i)} {rank_em} **{e['osu_username']}**\n"
                    f"  `{format_score(e['score'])}` • {format_acc(e['accuracy'])} • {miss_str} • `{e['mods']}`"
                )
            embed.description = "\n".join(lines)

        embed.set_footer(text=f"Mod vereiste: NF + {target_map['mod_category']}" 
                         if target_map["mod_category"] != "NM" else "Mod vereiste: NF")
        await interaction.followup.send(embed=embed)

    # ── Globaal LAN leaderboard (totale score) ───────────────────────────────

    @app_commands.command(name="rankings", description="Globaal LAN ranking — totale score over alle pools")
    async def rankings(self, interaction: discord.Interaction):
        await interaction.response.defer()

        rows = await self.bot.db.get_global_leaderboard(interaction.guild_id)
        if not rows:
            return await interaction.followup.send("Nog geen pool scores bijgehouden.")

        embed = discord.Embed(
            title="🏆 LAN Ranking — Totale Score",
            description="Gesommeerde beste scores per map uit alle pools",
            color=0xFFAA00
        )

        lines = []
        for i, r in enumerate(rows):
            if not r["total_score"]:
                continue
            fc_str = f" • {r['fc_count']} FC" if r["fc_count"] else ""
            lines.append(
                f"{medal(i)} **{r['osu_username']}**\n"
                f"  `{format_score(r['total_score'])}` pts • "
                f"{format_acc(r['avg_accuracy'])} gem. • "
                f"{r['maps_completed']} maps{fc_str}"
            )

        embed.description = "\n".join(lines) if lines else "Nog geen scores."
        await interaction.followup.send(embed=embed)

    # ── Einde LAN recap ──────────────────────────────────────────────────────

    @app_commands.command(name="lan_recap", description="Volledige einde-LAN samenvatting")
    async def lan_recap(self, interaction: discord.Interaction):
        await interaction.response.defer()

        players = await self.bot.db.get_all_players()
        if not players:
            return await interaction.followup.send("Geen spelers geregistreerd.")

        rows = await self.bot.db.get_global_leaderboard(interaction.guild_id)
        pools = await self.bot.db.get_all_pools(interaction.guild_id)

        # Maak per-player dicts
        player_data = {r["discord_id"]: r for r in rows}

        embed = discord.Embed(
            title="🎉 LAN Recap",
            color=0xFFAA00
        )
        embed.add_field(name="Spelers", value=str(len(players)), inline=True)
        embed.add_field(name="Pools", value=str(len(pools)), inline=True)

        # Rankings
        ranked = [r for r in rows if r["total_score"]]
        if ranked:
            ranking_lines = []
            for i, r in enumerate(ranked):
                ranking_lines.append(
                    f"{medal(i)} **{r['osu_username']}** — "
                    f"`{format_score(r['total_score'])}` pts • "
                    f"{format_acc(r['avg_accuracy'])} • {r['maps_completed']} maps"
                )
            embed.add_field(name="🏆 Eindstand", value="\n".join(ranking_lines), inline=False)

        # MVP: meeste FC's
        all_stats = []
        for p in players:
            s = await self.bot.db.get_player_stats_summary(p["discord_id"])
            if s:
                all_stats.append((p["osu_username"], s))

        if all_stats:
            top_fc = max(all_stats, key=lambda x: x[1]["fc_count"] or 0)
            top_acc = max(all_stats, key=lambda x: x[1]["avg_accuracy"] or 0)
            top_lazer = max(all_stats, key=lambda x: x[1]["lazer_scores"] or 0)

            embed.add_field(
                name="🏅 Awards",
                value=(
                    f"✨ **FC King**: {top_fc[0]} ({top_fc[1]['fc_count']} FC's)\n"
                    f"🎯 **Most Accurate**: {top_acc[0]} ({format_acc(top_acc[1]['avg_accuracy'])})\n"
                    f"🌐 **Lazer Gang**: {top_lazer[0]} ({top_lazer[1]['lazer_scores']} lazer scores)"
                ),
                inline=False
            )

        # Missers
        no_scores = [p["osu_username"] for p in players if p["discord_id"] not in player_data or not player_data.get(p["discord_id"], {}).get("total_score")]
        if no_scores:
            embed.add_field(
                name="😴 Geen pool scores",
                value=", ".join(no_scores),
                inline=False
            )

        await interaction.followup.send(embed=embed)

    # ── Per-pool recap ───────────────────────────────────────────────────────

    @app_commands.command(name="pool_recap", description="Winnaar per map in een pool")
    @app_commands.describe(pool_channel="Het pool channel")
    async def pool_recap(self, interaction: discord.Interaction, pool_channel: discord.TextChannel):
        await interaction.response.defer()

        pool = await self.bot.db.get_pool_by_channel(pool_channel.id)
        if not pool:
            return await interaction.followup.send("❌ Dit channel is geen pool.")

        rows = await self.bot.db.get_pool_leaderboard(pool["id"])
        maps_all = await self.bot.db.get_pool_maps(pool["id"])

        wins: dict[str, int] = defaultdict(int)
        maps_done: dict[int, dict] = {}

        for r in rows:
            if r["beatmap_id"] not in maps_done:
                maps_done[r["beatmap_id"]] = r  # eerste = hoogste score (gesorteerd DESC)

        for bm_id, winner in maps_done.items():
            wins[winner["osu_username"]] += 1

        embed = discord.Embed(
            title=f"🎵 {pool['name']} — Recap",
            color=0xFF66AA
        )
        embed.add_field(name="Maps in pool", value=str(len(maps_all)), inline=True)
        embed.add_field(name="Maps gespeeld", value=str(len(maps_done)), inline=True)

        # Wins per speler
        if wins:
            win_lines = sorted(wins.items(), key=lambda x: -x[1])
            embed.add_field(
                name="🏆 Map wins",
                value="\n".join(f"{medal(i)} **{name}**: {w} maps" for i, (name, w) in enumerate(win_lines)),
                inline=False
            )

        # Map winners lijst
        map_list = sorted(maps_done.values(), key=lambda x: (x["mod_category"], x["slot"]))
        lines = []
        for m in map_list:
            fc = " ✨" if m["count_miss"] == 0 else ""
            lines.append(
                f"`{m['slot']}` **{m['osu_username']}** — `{format_score(m['score'])}` {format_acc(m['accuracy'])}{fc}"
            )

        # Ongespelde maps
        played_ids = set(maps_done.keys())
        unplayed = [m for m in maps_all if m["beatmap_id"] not in played_ids]
        if unplayed:
            lines.append(f"\n_Niet gespeeld: {', '.join(m['slot'] for m in unplayed)}_")

        embed.add_field(name="📋 Map winnaars", value="\n".join(lines) or "—", inline=False)
        await interaction.followup.send(embed=embed)

    # ── Stat rankings (acc, fc, etc.) ────────────────────────────────────────

    @app_commands.command(name="stat_rankings", description="Rankings op een specifieke stat")
    @app_commands.choices(stat=[
        app_commands.Choice(name="Accuracy", value="accuracy"),
        app_commands.Choice(name="FC count", value="fc"),
        app_commands.Choice(name="Maps gespeeld", value="maps"),
        app_commands.Choice(name="Lazer scores", value="lazer"),
        app_commands.Choice(name="Stable scores", value="stable"),
    ])
    async def stat_rankings(self, interaction: discord.Interaction, stat: str):
        await interaction.response.defer()

        players = await self.bot.db.get_all_players()
        if not players:
            return await interaction.followup.send("Geen spelers geregistreerd.")

        all_stats = []
        for p in players:
            s = await self.bot.db.get_player_stats_summary(p["discord_id"])
            all_stats.append((p["osu_username"], s or {}))

        stat_map = {
            "accuracy": ("avg_accuracy", "Gem. Accuracy", lambda x: format_acc(x)),
            "fc":       ("fc_count",     "FC Count",      lambda x: f"{x} FC's"),
            "maps":     ("total_scores", "Scores totaal", lambda x: str(x)),
            "lazer":    ("lazer_scores", "Lazer scores",  lambda x: str(x)),
            "stable":   ("stable_scores","Stable scores", lambda x: str(x)),
        }
        key, label, fmt = stat_map[stat]
        sorted_stats = sorted(all_stats, key=lambda x: x[1].get(key) or 0, reverse=True)

        embed = discord.Embed(title=f"📊 Rankings — {label}", color=0x66AAFF)
        lines = []
        for i, (name, s) in enumerate(sorted_stats):
            val = s.get(key)
            if val is None:
                continue
            lines.append(f"{medal(i)} **{name}** — {fmt(val)}")

        embed.description = "\n".join(lines) if lines else "Geen data."
        await interaction.followup.send(embed=embed)


async def setup(bot):
    await bot.add_cog(StatsCog(bot))
