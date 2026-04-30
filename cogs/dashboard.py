import discord
from discord import app_commands
from discord.ext import commands
from collections import defaultdict
from datetime import datetime, timezone

MEDALS = ["🥇", "🥈", "🥉"]
MOD_CAT_EMOJI = {"NM": "🔵", "HD": "🟡", "HR": "🔴", "DT": "🟣", "FL": "⚫", "EZ": "🟢", "TB": "🏆"}


def fmt_score(score) -> str:
    return f"{int(score):,}".replace(",", ".") if score else "—"

def fmt_acc(acc) -> str:
    return f"{acc:.2f}%" if acc is not None else "—"

def fmt_time(seconds: int) -> str:
    if not seconds:
        return "0m"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    if h > 0:
        return f"{h}u {m}m"
    return f"{m}m"

def medal(i: int) -> str:
    return MEDALS[i] if i < len(MEDALS) else f"`#{i+1}`"


class DashboardCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── Setup command ────────────────────────────────────────────────────────

    @app_commands.command(name="setup_dashboard", description="Stel het dashboard channel in en maak de embeds aan")
    @app_commands.describe(channel="Het channel voor het dashboard (pools worden threads hiervan)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup_dashboard(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await interaction.response.defer(ephemeral=True)

        # Sla channel op
        await self.bot.db.set_main_channel(interaction.guild_id, channel.id)
        await self.bot.db.update_guild_settings(interaction.guild_id, main_channel_id=channel.id)

        # Maak LAN stats embed
        stats_embed = await self._build_stats_embed(interaction.guild_id)
        stats_msg = await channel.send(embed=stats_embed)

        # Maak pool leaderboard embed
        pool_embed = await self._build_pool_lb_embed(interaction.guild_id)
        pool_msg = await channel.send(embed=pool_embed)

        # Sla message IDs op
        await self.bot.db.save_dashboard_messages(interaction.guild_id, stats_msg.id, pool_msg.id)

        await interaction.followup.send(
            f"✅ Dashboard aangemaakt in {channel.mention}!\n"
            f"Stats embed: `{stats_msg.id}` • Pool LB embed: `{pool_msg.id}`"
        )

    @app_commands.command(name="refresh_dashboard", description="Forceer een update van het dashboard")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def refresh_dashboard(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.update_dashboard(interaction.guild_id)
        await interaction.followup.send("✅ Dashboard bijgewerkt.")

    # ── Core update logic ────────────────────────────────────────────────────

    async def update_dashboard(self, guild_id: int):
        """Update beide dashboard embeds. Wordt aangeroepen vanuit tracking na elke score."""
        settings = await self.bot.db.get_guild_settings(guild_id)
        channel_id = settings.get("main_channel_id")
        stats_msg_id = settings.get("lan_stats_message_id")
        pool_lb_msg_id = settings.get("pool_lb_message_id")

        if not channel_id or not stats_msg_id or not pool_lb_msg_id:
            return  # Dashboard nog niet opgezet

        channel = self.bot.get_channel(channel_id)
        if not channel:
            return

        # Update stats embed
        try:
            stats_embed = await self._build_stats_embed(guild_id)
            stats_msg = await channel.fetch_message(stats_msg_id)
            await stats_msg.edit(embed=stats_embed)
        except (discord.NotFound, Exception):
            pass

        # Update pool leaderboard embed
        try:
            pool_embed = await self._build_pool_lb_embed(guild_id)
            pool_msg = await channel.fetch_message(pool_lb_msg_id)
            await pool_msg.edit(embed=pool_embed)
        except (discord.NotFound, Exception):
            pass

    # ── Embed builders ───────────────────────────────────────────────────────

    async def _build_stats_embed(self, guild_id: int) -> discord.Embed:
        settings = await self.bot.db.get_guild_settings(guild_id)
        since = settings.get("lan_start_time")
        stats = await self.bot.db.get_dashboard_stats(guild_id, since=since)
        players = await self.bot.db.get_all_players()

        embed = discord.Embed(title="📊 LAN Stats", color=0xFFAA00)

        # Duur
        if since:
            since_aware = since.replace(tzinfo=timezone.utc) if since.tzinfo is None else since
            duration = datetime.now(timezone.utc) - since_aware
            h, rem = divmod(int(duration.total_seconds()), 3600)
            m = rem // 60
            embed.description = f"⏱️ LAN loopt al **{h}u {m}m**"

        if stats:
            playtime = stats["total_playtime_seconds"] or 0
            embed.add_field(name="👥 Spelers", value=str(len(players)), inline=True)
            embed.add_field(name="🎯 Scores gezet", value=str(stats["total_scores"] or 0), inline=True)
            embed.add_field(name="🎱 Pool plays", value=str(stats["pool_scores"] or 0), inline=True)
            embed.add_field(name="✨ FC's", value=str(stats["total_fcs"] or 0), inline=True)
            embed.add_field(name="🎯 Gem. accuracy", value=fmt_acc(stats["avg_accuracy"]), inline=True)
            embed.add_field(name="⏱️ Totale playtime", value=fmt_time(playtime), inline=True)
            embed.add_field(name="🗺️ Unieke pool maps", value=str(stats["unique_pool_maps"] or 0), inline=True)
            embed.add_field(name="🏆 Top score", value=fmt_score(stats["top_score"]), inline=True)

        embed.set_footer(text=f"Bijgewerkt: {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC")
        return embed

    async def _build_pool_lb_embed(self, guild_id: int) -> discord.Embed:
        rows = await self.bot.db.get_dashboard_pool_leaderboards(guild_id)
        pools = await self.bot.db.get_all_pools(guild_id)

        embed = discord.Embed(title="🏆 Pool Leaderboards — Gemiddelde Score", color=0xFF66AA)

        if not rows:
            embed.description = "Nog geen pool scores."
            embed.set_footer(text=f"Bijgewerkt: {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC")
            return embed

        # Groepeer per pool
        by_pool: dict = defaultdict(list)
        for r in rows:
            by_pool[r["pool_id"]].append(r)

        for pool in pools:
            entries = by_pool.get(pool["id"], [])
            if not entries:
                embed.add_field(
                    name=f"🎵 {pool['name']}",
                    value="_(nog geen scores)_",
                    inline=False
                )
                continue

            lines = []
            for i, e in enumerate(entries):  # al gesorteerd op avg_score DESC
                bar_done = e["maps_played"]
                bar_total = e["maps_total"] or 1
                pct = int(bar_done / bar_total * 5)
                bar = "█" * pct + "░" * (5 - pct)
                fc_str = f" • {e['fc_count']}FC" if e["fc_count"] else ""
                lines.append(
                    f"{medal(i)} **{e['osu_username']}** `{bar}` {bar_done}/{bar_total}\n"
                    f"  `{fmt_score(e['avg_score'])}` gem. • {fmt_acc(e['avg_accuracy'])}{fc_str}"
                )

            embed.add_field(
                name=f"🎵 {pool['name']}",
                value="\n".join(lines),
                inline=False
            )

        embed.set_footer(text=f"Bijgewerkt: {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC")
        return embed


async def setup(bot):
    await bot.add_cog(DashboardCog(bot))
