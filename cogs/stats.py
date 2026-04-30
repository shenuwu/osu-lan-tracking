import discord
from discord import app_commands
from discord.ext import commands
from collections import defaultdict

RANK_EMOJIS = {"XH": "🌟", "X": "⭐", "SH": "💿", "S": "💽", "A": "🟢", "B": "🔵", "C": "🟡", "D": "🔴", "F": "💀"}
MEDALS = ["🥇", "🥈", "🥉"]
MOD_CAT_EMOJI = {"NM": "🔵", "HD": "🟡", "HR": "🔴", "DT": "🟣", "FL": "⚫", "EZ": "🟢", "TB": "🏆"}

def fmt_score(score) -> str:
    return f"{int(score):,}".replace(",", ".") if score else "—"

def fmt_acc(acc) -> str:
    return f"{acc:.2f}%" if acc is not None else "—"

def medal(i: int) -> str:
    return MEDALS[i] if i < len(MEDALS) else f"`#{i+1}`"

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


class StatsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="leaderboard", description="Pool leaderboard — beste scores per map")
    @app_commands.describe(pool="De pool", category="Filter: NM, HD, HR of DT")
    @app_commands.autocomplete(pool=pool_autocomplete)
    async def leaderboard(self, interaction: discord.Interaction, pool: str, category: str = None):
        await interaction.response.defer()
        pool_row = await get_pool(self.bot, interaction, pool)
        if not pool_row:
            return

        rows = await self.bot.db.get_pool_leaderboard(pool_row["id"])
        if not rows:
            return await interaction.followup.send(f"Nog geen scores in **{pool_row['name']}**.")

        maps: dict = defaultdict(list)
        map_meta: dict = {}
        for r in rows:
            if category and r["mod_category"].upper() != category.upper():
                continue
            maps[r["beatmap_id"]].append(r)
            if r["beatmap_id"] not in map_meta:
                map_meta[r["beatmap_id"]] = {
                    "title": r["title"], "version": r["version"],
                    "slot": r["slot"], "mod_category": r["mod_category"],
                }

        if not maps:
            return await interaction.followup.send("Geen scores voor dit filter.")

        embed = discord.Embed(
            title=f"🏆 {pool_row['name']}{'  —  ' + category.upper() if category else ''}",
            color=0xFF66AA
        )
        embed.set_footer(text="NF scores • beste score per speler per map")

        for beatmap_id, meta in sorted(map_meta.items(), key=lambda x: (x[1]["mod_category"], x[1]["slot"])):
            entries = maps[beatmap_id]
            cat_emoji = MOD_CAT_EMOJI.get(meta["mod_category"], "⚪")
            title_s = meta["title"][:28] + "…" if len(meta["title"]) > 28 else meta["title"]
            lines = [
                f"{medal(i)} **{e['osu_username']}** — `{fmt_score(e['score'])}` • "
                f"{fmt_acc(e['accuracy'])} • {'FC ✨' if not e['count_miss'] else str(e['count_miss'])+'x miss'} • `{e['mods']}`"
                for i, e in enumerate(entries)
            ]
            embed.add_field(
                name=f"{cat_emoji} `{meta['slot']}` {title_s} [{meta['version']}]",
                value="\n".join(lines) or "—", inline=False
            )
            if len(embed.fields) >= 24:
                embed.set_footer(text="⚠️ Te veel maps — gebruik category filter")
                break

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="map_leaderboard", description="Leaderboard van 1 specifieke map")
    @app_commands.describe(pool="De pool", slot="Slot bijv. NM1, HD2")
    @app_commands.autocomplete(pool=pool_autocomplete)
    async def map_leaderboard(self, interaction: discord.Interaction, pool: str, slot: str):
        await interaction.response.defer()
        pool_row = await get_pool(self.bot, interaction, pool)
        if not pool_row:
            return

        maps = await self.bot.db.get_pool_maps(pool_row["id"])
        target = next((m for m in maps if m["slot"].upper() == slot.upper()), None)
        if not target:
            return await interaction.followup.send(f"❌ Slot `{slot}` niet gevonden.")

        entries = await self.bot.db.get_map_leaderboard(pool_row["id"], target["beatmap_id"])
        cat_emoji = MOD_CAT_EMOJI.get(target["mod_category"], "⚪")
        embed = discord.Embed(
            title=f"{cat_emoji} `{slot}` — {target['artist']} - {target['title']} [{target['version']}]",
            url=f"https://osu.ppy.sh/beatmaps/{target['beatmap_id']}",
            color=0x66AAFF
        )
        if not entries:
            embed.description = "Nog geen scores op deze map."
        else:
            lines = [
                f"{medal(i)} {RANK_EMOJIS.get(e['rank'], '❓')} **{e['osu_username']}**\n"
                f"  `{fmt_score(e['score'])}` • {fmt_acc(e['accuracy'])} • "
                f"{'FC ✨' if not e['count_miss'] else str(e['count_miss'])+'x miss'} • `{e['mods']}`"
                for i, e in enumerate(entries)
            ]
            embed.description = "\n".join(lines)
        embed.set_footer(
            text=f"Vereiste mods: NF + {target['mod_category']}"
            if target["mod_category"] != "NM" else "Vereiste mods: NF"
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="rankings", description="Globaal LAN ranking — totale score")
    async def rankings(self, interaction: discord.Interaction):
        await interaction.response.defer()
        rows = await self.bot.db.get_global_leaderboard(interaction.guild_id)
        if not rows:
            return await interaction.followup.send("Nog geen pool scores.")

        lines = [
            f"{medal(i)} **{r['osu_username']}**\n"
            f"  `{fmt_score(r['total_score'])}` pts • {fmt_acc(r['avg_accuracy'])} gem. • "
            f"{r['maps_completed']} maps" + (f" • {r['fc_count']} FC" if r["fc_count"] else "")
            for i, r in enumerate(rows) if r["total_score"]
        ]
        embed = discord.Embed(
            title="🏆 LAN Ranking — Totale Score",
            description="\n".join(lines) if lines else "Nog geen scores.",
            color=0xFFAA00
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="lan_recap", description="Volledige einde-LAN samenvatting")
    async def lan_recap(self, interaction: discord.Interaction):
        await interaction.response.defer()
        players = await self.bot.db.get_all_players()
        if not players:
            return await interaction.followup.send("Geen spelers geregistreerd.")

        rows = await self.bot.db.get_global_leaderboard(interaction.guild_id)
        pools = await self.bot.db.get_all_pools(interaction.guild_id)
        player_data = {r["discord_id"]: r for r in rows}

        embed = discord.Embed(title="🎉 LAN Recap", color=0xFFAA00)
        embed.add_field(name="Spelers", value=str(len(players)), inline=True)
        embed.add_field(name="Pools", value=str(len(pools)), inline=True)

        ranked = [r for r in rows if r["total_score"]]
        if ranked:
            embed.add_field(
                name="🏆 Eindstand",
                value="\n".join(
                    f"{medal(i)} **{r['osu_username']}** — `{fmt_score(r['total_score'])}` pts • "
                    f"{fmt_acc(r['avg_accuracy'])} • {r['maps_completed']} maps"
                    for i, r in enumerate(ranked)
                ),
                inline=False
            )

        all_stats = [(p["osu_username"], await self.bot.db.get_player_stats_summary(p["discord_id"])) for p in players]
        all_stats = [(n, s) for n, s in all_stats if s]
        if all_stats:
            top_fc    = max(all_stats, key=lambda x: x[1]["fc_count"] or 0)
            top_acc   = max(all_stats, key=lambda x: x[1]["avg_accuracy"] or 0)
            top_lazer = max(all_stats, key=lambda x: x[1]["lazer_scores"] or 0)
            embed.add_field(
                name="🏅 Awards",
                value=(
                    f"✨ **FC King**: {top_fc[0]} ({top_fc[1]['fc_count']} FC's)\n"
                    f"🎯 **Most Accurate**: {top_acc[0]} ({fmt_acc(top_acc[1]['avg_accuracy'])})\n"
                    f"🌐 **Lazer Gang**: {top_lazer[0]} ({top_lazer[1]['lazer_scores']} lazer scores)"
                ),
                inline=False
            )

        no_scores = [p["osu_username"] for p in players if not player_data.get(p["discord_id"], {}).get("total_score")]
        if no_scores:
            embed.add_field(name="😴 Geen pool scores", value=", ".join(no_scores), inline=False)

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="pool_recap", description="Winnaar per map in een pool")
    @app_commands.describe(pool="De pool")
    @app_commands.autocomplete(pool=pool_autocomplete)
    async def pool_recap(self, interaction: discord.Interaction, pool: str):
        await interaction.response.defer()
        pool_row = await get_pool(self.bot, interaction, pool)
        if not pool_row:
            return

        rows = await self.bot.db.get_pool_leaderboard(pool_row["id"])
        maps_all = await self.bot.db.get_pool_maps(pool_row["id"])

        wins: dict = defaultdict(int)
        maps_done: dict = {}
        for r in rows:
            if r["beatmap_id"] not in maps_done:
                maps_done[r["beatmap_id"]] = r
        for _, winner in maps_done.items():
            wins[winner["osu_username"]] += 1

        embed = discord.Embed(title=f"🎵 {pool_row['name']} — Recap", color=0xFF66AA)
        embed.add_field(name="Maps in pool", value=str(len(maps_all)), inline=True)
        embed.add_field(name="Maps gespeeld", value=str(len(maps_done)), inline=True)

        if wins:
            embed.add_field(
                name="🏆 Map wins",
                value="\n".join(f"{medal(i)} **{n}**: {w} maps" for i, (n, w) in enumerate(sorted(wins.items(), key=lambda x: -x[1]))),
                inline=False
            )

        lines = []
        for m in sorted(maps_done.values(), key=lambda x: (x["mod_category"], x["slot"])):
            fc = " ✨" if m["count_miss"] == 0 else ""
            lines.append(f"`{m['slot']}` **{m['osu_username']}** — `{fmt_score(m['score'])}` {fmt_acc(m['accuracy'])}{fc}")

        unplayed = [m["slot"] for m in maps_all if m["beatmap_id"] not in maps_done]
        if unplayed:
            lines.append(f"\n_Niet gespeeld: {', '.join(unplayed)}_")

        embed.add_field(name="📋 Map winnaars", value="\n".join(lines) or "—", inline=False)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="stat_rankings", description="Rankings op een specifieke stat")
    @app_commands.choices(stat=[
        app_commands.Choice(name="Accuracy",      value="accuracy"),
        app_commands.Choice(name="FC count",      value="fc"),
        app_commands.Choice(name="Maps gespeeld", value="maps"),
        app_commands.Choice(name="Lazer scores",  value="lazer"),
        app_commands.Choice(name="Stable scores", value="stable"),
    ])
    async def stat_rankings(self, interaction: discord.Interaction, stat: str):
        await interaction.response.defer()
        players = await self.bot.db.get_all_players()
        if not players:
            return await interaction.followup.send("Geen spelers geregistreerd.")

        all_stats = [(p["osu_username"], (await self.bot.db.get_player_stats_summary(p["discord_id"])) or {}) for p in players]
        stat_map = {
            "accuracy": ("avg_accuracy",  "Gem. Accuracy",  fmt_acc),
            "fc":       ("fc_count",      "FC Count",       lambda x: f"{x} FC's"),
            "maps":     ("total_scores",  "Scores totaal",  str),
            "lazer":    ("lazer_scores",  "Lazer scores",   str),
            "stable":   ("stable_scores", "Stable scores",  str),
        }
        key, label, fmt = stat_map[stat]
        sorted_stats = sorted(all_stats, key=lambda x: x[1].get(key) or 0, reverse=True)
        lines = [f"{medal(i)} **{n}** — {fmt(s[key])}" for i, (n, s) in enumerate(sorted_stats) if s.get(key) is not None]
        embed = discord.Embed(title=f"📊 Rankings — {label}", description="\n".join(lines) or "Geen data.", color=0x66AAFF)
        await interaction.followup.send(embed=embed)


    @app_commands.command(name="all_scores", description="Alle pool scores van iedereen, gesorteerd op score")
    async def all_scores(self, interaction: discord.Interaction):
        await interaction.response.defer()
        rows = await self.bot.db.get_all_pool_scores_leaderboard(interaction.guild_id)
        if not rows:
            return await interaction.followup.send("Nog geen pool scores.")

        lines = []
        for i, r in enumerate(rows[:20]):  # max 20 zodat embed niet te lang is
            title_s = r["title"][:22] + "…" if len(r["title"]) > 22 else r["title"]
            miss_str = "FC ✨" if not r["count_miss"] else f"{r['count_miss']}x miss"
            lines.append(
                f"`#{i+1}` **{r['osu_username']}** — `{r['slot']}` {title_s}\n"
                f"  `{fmt_score(r['score'])}` • {fmt_acc(r['accuracy'])} • {miss_str} • `{r['mods']}`"
            )

        embed = discord.Embed(
            title="🏅 Alle pool scores",
            description="\n".join(lines),
            color=0xFFAA00
        )
        if len(rows) > 20:
            embed.set_footer(text=f"Top 20 van {len(rows)} scores")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="avg_leaderboard", description="Leaderboard op gemiddelde pool score")
    async def avg_leaderboard(self, interaction: discord.Interaction):
        await interaction.response.defer()
        rows = await self.bot.db.get_avg_leaderboard(interaction.guild_id)
        if not rows:
            return await interaction.followup.send("Nog geen pool scores.")

        lines = [
            f"{medal(i)} **{r['osu_username']}**\n"
            f"  gem. `{fmt_score(r['avg_score'])}` • {fmt_acc(r['avg_accuracy'])} • "
            f"{r['maps_played']} maps • {r['fc_count']} FC's"
            for i, r in enumerate(rows)
        ]
        embed = discord.Embed(
            title="📊 Gemiddelde Pool Score",
            description="\n".join(lines),
            color=0x66AAFF
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="lan_start", description="Sla het starttijdstip van de LAN op")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def lan_start(self, interaction: discord.Interaction):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        await self.bot.db.update_guild_settings(interaction.guild_id, lan_start_time=now)
        await interaction.response.send_message(
            f"✅ LAN gestart op **{now.strftime('%d-%m-%Y %H:%M')} UTC**. Vanaf nu worden stats bijgehouden.",
            ephemeral=False
        )

    @app_commands.command(name="lan_stats", description="Statistieken over de LAN-sessie")
    @app_commands.describe(member="Laat leeg voor globale stats, of kies een speler")
    async def lan_stats(self, interaction: discord.Interaction, member: discord.Member = None):
        await interaction.response.defer()
        from datetime import datetime, timezone

        settings = await self.bot.db.get_guild_settings(interaction.guild_id)
        since = settings.get("lan_start_time")

        if member:
            # Per-speler stats
            player = await self.bot.db.get_player(member.id)
            if not player:
                return await interaction.followup.send(f"❌ {member.display_name} is niet geregistreerd.")

            players = await self.bot.db.get_lan_stats_per_player(interaction.guild_id, since=since)
            pdata = next((p for p in players if p["discord_id"] == member.id), None)

            if not pdata:
                return await interaction.followup.send(f"Geen data gevonden voor **{player['osu_username']}**.")

            pool_summary = await self.bot.db.get_player_pool_summary(member.id, interaction.guild_id)

            embed = discord.Embed(
                title=f"📊 LAN Stats — {player['osu_username']}",
                color=0xFF66AA
            )
            if since:
                embed.set_footer(text=f"Sinds {since.strftime('%d-%m %H:%M')} UTC")

            embed.add_field(name="Scores gezet", value=str(pdata["total_scores"] or 0), inline=True)
            embed.add_field(name="Pool scores", value=str(pdata["pool_scores"] or 0), inline=True)
            embed.add_field(name="FC's", value=str(pdata["fc_count"] or 0), inline=True)
            embed.add_field(name="Gem. accuracy", value=fmt_acc(pdata["avg_accuracy"]) if pdata["avg_accuracy"] else "—", inline=True)
            embed.add_field(name="Top score", value=fmt_score(pdata["top_score"]) if pdata["top_score"] else "—", inline=True)
            embed.add_field(
                name="Client",
                value=f"🌐 Lazer: {pdata['lazer_scores']} • 💾 Stable: {pdata['stable_scores']}",
                inline=True
            )

            if pool_summary:
                lines = []
                for ps in pool_summary:
                    done = ps["maps_done"] or 0
                    total = ps["maps_total"] or 0
                    pct = int(done / total * 10) if total > 0 else 0
                    bar = "█" * pct + "░" * (10 - pct)
                    lines.append(f"**{ps['pool_name']}**: `{bar}` {done}/{total}")
                embed.add_field(name="Pool voortgang", value="\n".join(lines), inline=False)

        else:
            # Globale stats
            global_stats = await self.bot.db.get_lan_stats_global(interaction.guild_id, since=since)
            per_player = await self.bot.db.get_lan_stats_per_player(interaction.guild_id, since=since)

            embed = discord.Embed(title="📊 LAN Stats — Globaal", color=0xFFAA00)
            if since:
                duration = datetime.now(timezone.utc) - since
                hours, remainder = divmod(int(duration.total_seconds()), 3600)
                minutes = remainder // 60
                embed.set_footer(text=f"LAN gestart {since.strftime('%d-%m %H:%M')} UTC • {hours}u {minutes}m geleden")
            else:
                embed.set_footer(text="Gebruik /lan_start om een starttijdstip in te stellen")

            if global_stats:
                embed.add_field(name="Scores gezet", value=str(global_stats["total_scores"] or 0), inline=True)
                embed.add_field(name="Pool scores", value=str(global_stats["pool_scores"] or 0), inline=True)
                embed.add_field(name="FC's gezet", value=str(global_stats["fc_count"] or 0), inline=True)
                embed.add_field(name="Gem. accuracy", value=fmt_acc(global_stats["avg_accuracy"]) if global_stats["avg_accuracy"] else "—", inline=True)
                embed.add_field(name="Actieve spelers", value=str(global_stats["active_players"] or 0), inline=True)
                embed.add_field(name="Top score", value=fmt_score(global_stats["top_score"]) if global_stats["top_score"] else "—", inline=True)

            if per_player:
                lines = []
                for p in per_player:
                    if not p["total_scores"]:
                        continue
                    lines.append(
                        f"**{p['osu_username']}** — {p['total_scores']} scores • "
                        f"{p['fc_count']} FC's • {fmt_acc(p['avg_accuracy']) if p['avg_accuracy'] else '—'}"
                    )
                if lines:
                    embed.add_field(name="👥 Per speler", value="\n".join(lines), inline=False)

        await interaction.followup.send(embed=embed)


async def setup(bot):
    await bot.add_cog(StatsCog(bot))
