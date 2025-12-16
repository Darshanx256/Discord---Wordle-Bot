import os
import sys
import asyncio
import datetime
import random
import discord
from discord.ext import commands, tasks
from supabase import create_client, Client

from src.config import SUPABASE_URL, SUPABASE_KEY, SECRET_FILE, VALID_FILE, CLASSIC_FILE, ROTATING_ACTIVITIES
from src.database import fetch_user_profile_v2
from src.utils import EMOJIS, get_badge_emoji

# ========= BOT SETUP =========
class WordleBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents(guilds=True))
        self.games = {}
        self.solo_games = {}
        self.custom_games = {}  # Custom mode games
        self.stopped_games = set()
        self.egg_cooldowns = {}
        self.secrets = []
        self.hard_secrets = []
        self.valid_set = set()
        self.name_cache = {}
        self.supabase_client: Client = None

    async def setup_hook(self):
        self.load_local_data()
        self.setup_db()
        # Load cogs first so their app-commands are registered before syncing
        await self.load_cogs()
        await self.tree.sync()
        self.cleanup_task.start()
        self.cache_clear_task.start()
        self.db_ping_task.start()
        self.activity_loop.start()
        print(f"✅ Ready! {len(self.secrets)} simple secrets, {len(self.hard_secrets)} classic secrets.")

    async def load_cogs(self):
        """Load all cogs from src/cogs/ directory."""
        cogs_dir = "src/cogs"
        for filename in os.listdir(cogs_dir):
            if filename.endswith(".py") and filename != "__init__.py":
                cog_name = filename[:-3]
                try:
                    await self.load_extension(f"src.cogs.{cog_name}")
                    print(f"✅ Loaded cog: {cog_name}")
                except Exception as e:
                    print(f"❌ Failed to load cog {cog_name}: {e}")

    async def close(self):
        await super().close()

    def load_local_data(self):
        """Load word lists from files."""
        if os.path.exists(SECRET_FILE):
            with open(SECRET_FILE, "r", encoding="utf-8") as f:
                self.secrets = [w.strip().lower() for w in f if len(w.strip()) == 5]
        else:
            self.secrets = []

        if os.path.exists(VALID_FILE):
            with open(VALID_FILE, "r", encoding="utf-8") as f:
                self.valid_set = {w.strip().lower() for w in f if len(w.strip()) == 5}
        else:
            self.valid_set = set()

        if os.path.exists(CLASSIC_FILE):
            with open(CLASSIC_FILE, "r", encoding="utf-8") as f:
                self.hard_secrets = [w.strip().lower() for w in f if len(w.strip()) == 5]
        else:
            self.hard_secrets = []

        self.valid_set.update(self.secrets)
        self.valid_set.update(self.hard_secrets)

    def setup_db(self):
        """Initialize Supabase client."""
        print("Connecting to Supabase client...")
        try:
            self.supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
            response = self.supabase_client.from_('scores').select('count', count='exact').limit(0).execute()
            
            if response.data is not None:
                print("✅ Supabase client ready and tables accessible.")
            else:
                raise Exception("Failed to confirm Supabase table access.")
        except Exception as e:
            print(f"❌ FATAL DB ERROR during Supabase setup: {e}")
            sys.exit(1)

    @tasks.loop(minutes=60)
    async def activity_loop(self):
        """Rotates the bot's activity status every hour."""
        await self.wait_until_ready()
        if ROTATING_ACTIVITIES:
            act_data = random.choice(ROTATING_ACTIVITIES)
            activity = discord.Activity(type=act_data["type"], name=act_data["name"])
            await self.change_presence(activity=activity)

    @tasks.loop(minutes=60)
    async def cleanup_task(self):
        """Clean up stale games and solo games."""
        now = datetime.datetime.now()
        to_remove = []
        
        for cid, game in self.games.items():
            delta = now - game.last_interaction
            if delta.total_seconds() > 86400:  # 24 hours
                to_remove.append(cid)
                try:
                    channel = self.get_channel(cid)
                    if channel:
                        embed = discord.Embed(
                            title="⏰ Time's Up!",
                            description=f"Game timed out.\nThe word was **{game.secret.upper()}**.",
                            color=discord.Color.dark_grey()
                        )
                        await channel.send(embed=embed)
                        await asyncio.sleep(1)
                except:
                    pass

        for cid in to_remove:
            self.games.pop(cid, None)

        # Clean up custom games
        custom_remove = []
        for cid, game in self.custom_games.items():
            delta = now - game.last_interaction
            if delta.total_seconds() > 86400:  # 24 hours
                custom_remove.append(cid)

        for cid in custom_remove:
            self.custom_games.pop(cid, None)

        solo_remove = []
        for uid, sgame in self.solo_games.items():
            delta = now - sgame.last_interaction
            if delta.total_seconds() > 86400:
                solo_remove.append(uid)

        for uid in solo_remove:
            self.solo_games.pop(uid, None)

    @tasks.loop(hours=48)
    async def cache_clear_task(self):
        """Clear name cache every 2 days to ensure fresh data."""
        await self.wait_until_ready()
        old_size = len(self.name_cache)
        self.name_cache.clear()
        print(f"🗑️ Cache cleared: removed {old_size} cached names at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    @tasks.loop(hours=96)
    async def db_ping_task(self):
        """Ping Supabase every 4 days to prevent project freeze."""
        await self.wait_until_ready()
        if self.supabase_client:
            try:
                def ping_db_sync():
                    self.supabase_client.table('scores').select('count', count='exact').limit(0).execute()

                await asyncio.to_thread(ping_db_sync)
                print(f"✅ DB Ping Task: Successfully pinged Supabase at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            except Exception as e:
                print(f"⚠️ DB Ping Task Failed: {e}")


# Initialize Bot
bot = WordleBot()


# Run bot
def main():
    TOKEN = os.getenv('DISCORD_TOKEN')
    if not TOKEN:
        print("❌ DISCORD_TOKEN not set in environment variables.")
        sys.exit(1)
    bot.run(TOKEN)


if __name__ == "__main__":
    main()


@bot.tree.command(name="shop", description="Exchange collected items for badges.")
async def shop(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    
    p = fetch_user_profile_v2(bot, interaction.user.id)
    if not p: return await interaction.followup.send("Play some games first!", ephemeral=True)
    
    eggs = p.get('eggs', {}) or {}
    duck_count = int(eggs.get('duck', 0))
    dragon_count = int(eggs.get('dragon', 0))
    candy_count = int(eggs.get('candy', 0))
    
    # Simple logic: Toggle Badge if requirements met
    # Prompt: "purchase requires ... duck - 4x ... shows up next to name".
    
    view = discord.ui.View()
    
    async def buy_duck(inter: discord.Interaction):
        if duck_count >= 4:
            bot.supabase_client.table('user_stats_v2').update({'active_badge': 'duck_lord_badge'}).eq('user_id', interaction.user.id).execute()
            await inter.response.send_message("✅ Equipped Duck Lord Badge!", ephemeral=True)
        else:
            await inter.response.send_message(f"❌ Need 4 Ducks. You have {duck_count}.", ephemeral=True)

    async def buy_dragon(inter: discord.Interaction):
        if dragon_count >= 2:
            bot.supabase_client.table('user_stats_v2').update({'active_badge': 'dragon_slayer_badge'}).eq('user_id', interaction.user.id).execute()
            await inter.response.send_message("✅ Equipped Dragon Slayer Badge!", ephemeral=True)
        else:
            await inter.response.send_message(f"❌ Need 2 Dragons. You have {dragon_count}.", ephemeral=True)

    async def buy_candy(inter: discord.Interaction):
        if candy_count >= 3:
            bot.supabase_client.table('user_stats_v2').update({'active_badge': 'candy_rush_badge'}).eq('user_id', interaction.user.id).execute()
            await inter.response.send_message("✅ Equipped Sugar Rush Badge!", ephemeral=True)
        else:
            await inter.response.send_message(f"❌ Need 3 Candies. You have {candy_count}.", ephemeral=True)

    async def unequip(inter: discord.Interaction):
        current_badge = p.get('active_badge')
        if not current_badge:
            await inter.response.send_message("⚠️ No badge equipped.", ephemeral=True)
        else:
            bot.supabase_client.table('user_stats_v2').update({'active_badge': None}).eq('user_id', interaction.user.id).execute()
            await inter.response.send_message("✅ Badge unequipped.", ephemeral=True)
               
    b1 = discord.ui.Button(label="Duck Lord Badge (4 Ducks)", style=discord.ButtonStyle.primary, disabled=(duck_count < 4))
    b1.callback = buy_duck
    
    b2 = discord.ui.Button(label="Dragon Slayer Badge (2 Dragons)", style=discord.ButtonStyle.danger, disabled=(dragon_count < 2))
    b2.callback = buy_dragon
    
    b3 = discord.ui.Button(label="Sugar Rush Badge (3 Candies)", style=discord.ButtonStyle.success, disabled=(candy_count < 3))
    b3.callback = buy_candy

    b4 = discord.ui.Button(label="Unequip Badge", style=discord.ButtonStyle.secondary)
    b4.callback = unequip
    
    view.add_item(b1)
    view.add_item(b2)
    view.add_item(b3)
    view.add_item(b4)
    
    embed = discord.Embed(title="🛒 Collection Shop", description="Equip badges based on your findings!", color=discord.Color.gold())
    duck_emoji = EMOJIS.get("duck", "🦆")
    dragon_emoji = EMOJIS.get("dragon", "🐲")
    candy_emoji = EMOJIS.get("candy", "🍬")
    embed.add_field(name="Your Inventory", value=f"{duck_emoji} Ducks: {duck_count}\n{dragon_emoji} Dragons: {dragon_count}\n{candy_emoji} Candies: {candy_count}", inline=False)

    await interaction.followup.send(embed=embed, view=view, ephemeral=True)

