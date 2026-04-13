import discord
from discord.ext import commands
from discord import app_commands


class PlayerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @property
    def db(self):
        return self.bot.db

    @property
    def osu(self):
        return self.bot.osu

    @app_commands.command(name="register", description="Koppel je Discord account aan je osu! account voor de LAN tracker")
    @app_commands.describe(osu_username="Je osu! gebruikersnaam")
    async def register(self, interaction: discord.Interaction, osu_username: str):
        await interaction.response.defer(ephemeral=True)
        user_data = await self.osu.get_user(osu_username)
        if not user_data:
            return await interaction.followup.send(f"❌ osu! gebruiker `{osu_username}` niet gevonden.", ephemeral=True)

        await self.db.add_player(interaction.user.id, user_data["username"], user_data["id"], interaction.user.id)

        embed = discord.Embed(
            title="✅ Geregistreerd!",
            description=f"Je Discord is nu gekoppeld aan **{user_data['username']}**",
            color=0x50FA7B
        )
        embed.set_thumbnail(url=f"https://s.ppy.sh/a/{user_data['id']}")
        embed.add_field(name="osu! Rank", value=f"#{user_data.get('statistics', {}).get('global_rank', 'N/A'):,}" if user_data.get('statistics', {}).get('global_rank') else "N/A", inline=True)
        embed.add_field(name="PP", value=f"{user_data.get('statistics', {}).get('pp', 0):.0f}pp", inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="unregister", description="Verwijder jezelf uit de LAN tracker")
    async def unregister(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        player = await self.db.get_player(interaction.user.id)
        if not player:
            return await interaction.followup.send("❌ Je bent niet geregistreerd.", ephemeral=True)
        await self.db.remove_player(interaction.user.id)
        await interaction.followup.send(f"✅ Je account (**{player['osu_username']}**) is verwijderd uit de tracker.", ephemeral=True)

    @app_commands.command(name="profile", description="Bekijk je eigen LAN stats")
    async def profile(self, interaction: discord.Interaction):
        await interaction.response.defer()
        player = await self.db.get_player(interaction.user.id)
        if not player:
            return await interaction.followup.send("❌ Je bent niet geregistreerd. Gebruik `/register` eerst.")

        scores = await self.db.get_player_stats(interaction.user.id)
        if not scores:
            return await interaction.followup.send(f"Geen scores gevonden voor **{player['osu_username']}** op deze LAN.")

        passed = [s for s in scores if s["is_pass"]]
        avg_acc = sum(s["accuracy"] for s in passed) / len(passed) if passed else 0
        top_score = max((s["score"] for s in passed), default=0)
        total_score = sum(s["score"] for s in scores)
        fc_count = sum(1 for s in passed if s["count_miss"] == 0)
        s_count = sum(1 for s in passed if s["rank"] in ("S", "SS", "X", "XH", "SH"))

        embed = discord.Embed(
            title=f"🎮 {player['osu_username']} — LAN Profiel",
            color=0xBD93F9
        )
        embed.set_thumbnail(url=f"https://s.ppy.sh/a/{player['osu_id']}")
        embed.add_field(name="Maps gespeeld", value=str(len(scores)), inline=True)
        embed.add_field(name="Passes", value=str(len(passed)), inline=True)
        embed.add_field(name="Gem. Accuracy", value=f"{avg_acc:.2f}%", inline=True)
        embed.add_field(name="Top Score", value=f"`{top_score:,}`", inline=True)
        embed.add_field(name="Totaal Score", value=f"`{total_score:,}`", inline=True)
        embed.add_field(name="FC's", value=str(fc_count), inline=True)
        embed.add_field(name="S+ Ranks", value=str(s_count), inline=True)
        embed.set_footer(text="osu! LAN Tracker")
        await interaction.followup.send(embed=embed)


async def setup(bot):
    await bot.add_cog(PlayerCog(bot))
