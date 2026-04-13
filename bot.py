import discord
from discord.ext import commands
import os
import asyncio
import logging
from database import Database
from dotenv import load_dotenv

load_dotenv()

# Logging instellen — Railway toont alles wat naar stdout/stderr gaat
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("bot")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
db = Database()

@bot.event
async def on_ready():
    await db.init()
    logger.info(f"Bot online als {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        logger.info(f"{len(synced)} slash commands gesynchroniseerd")
    except Exception as e:
        logger.error(f"Sync error: {e}")

@bot.event
async def on_error(event, *args, **kwargs):
    logger.exception(f"Onverwachte fout in event '{event}'")

async def main():
    async with bot:
        logger.info("Extensions laden...")
        await bot.load_extension("cogs.admin")
        logger.info("cogs.admin geladen")
        await bot.load_extension("cogs.player")
        logger.info("cogs.player geladen")
        await bot.load_extension("cogs.stats")
        logger.info("cogs.stats geladen")
        await bot.load_extension("cogs.tracking")
        logger.info("cogs.tracking geladen")
        logger.info("Bot wordt gestart...")
        await bot.start(os.getenv("DISCORD_TOKEN"))

if __name__ == "__main__":
    asyncio.run(main())
