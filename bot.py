import discord
from discord.ext import commands
import os
import asyncio
from database import Database
from dotenv import load_dotenv

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
db = Database()

@bot.event
async def on_ready():
    await db.init()
    print(f"Bot online als {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"{len(synced)} slash commands gesynchroniseerd")
    except Exception as e:
        print(f"Sync error: {e}")

async def main():
    async with bot:
        await bot.load_extension("cogs.admin")
        await bot.load_extension("cogs.player")
        await bot.load_extension("cogs.stats")
        await bot.load_extension("cogs.tracking")
        await bot.start(os.getenv("DISCORD_TOKEN"))

if __name__ == "__main__":
    asyncio.run(main())
