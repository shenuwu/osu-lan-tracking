import os
import discord
from discord import app_commands


def is_admin(interaction: discord.Interaction) -> bool:
    """True als de user manage_guild heeft OF de bot owner is."""
    owner_id = os.getenv("OWNER_ID")
    if owner_id and interaction.user.id == int(owner_id):
        return True
    return interaction.user.guild_permissions.manage_guild


def admin_check():
    """app_commands check decorator die owner bypass ondersteunt."""
    return app_commands.check(is_admin)
