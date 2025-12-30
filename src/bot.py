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
        # We need message_content intent for the -g prefix shortcut
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True

        super().__init__(command_prefix=self.get_custom_prefix, intents=intents, max_messages=10)
        self.games = {}
        self.solo_games = {}
        self.custom_games = {}  # Custom mode games
        self.race_sessions = {}  # Race mode lobbies
        self.stopped_games = set()
        self.egg_cooldowns = {}
        self.secrets = []
        self.hard_secrets = []
        self.valid_set = set()
        self.name_cache = {}
        self.supabase_client: Client = None
        self.banned_users = set()  # Banned user IDs

    def get_custom_prefix(self, bot, message):
        """Only allow '-' as a prefix if it's followed by 'g' (the guess shortcut)."""
        # Global ban check for prefix commands
        if message.author.id in self.banned_users:
            return []

        content = message.content.lower()
        if content.startswith("-g ") or content.startswith("-g"):
            # Only allow if a game is active
            cid = message.channel.id
            uid = message.author.id
            if (cid in self.games) or (cid in self.custom_games) or (uid in self.solo_games):
                return "-"
        
        # No prefix for anything else (makes the bot essentially slash-only except for -g)
        return []

    async def on_command_error(self, ctx, error):
        """Silently ignore command errors from unintended prefix triggers."""
        if isinstance(error, commands.CommandNotFound):
            return # Silence "-wordle" or other typos
        if isinstance(error, commands.CheckFailure):
            return
        
        # For other errors, log them if they aren't interaction timeouts
        if "Unknown interaction" in str(error):
            return
            
        print(f"⚠️ Command Error in {ctx.command}: {error}")

    async def setup_hook(self):
        self.load_local_data()
        self.load_banned_users()
        self.setup_db()
        
        # Global Ban Check for Slash Commands
        @self.tree.interaction_check
        async def global_ban_check(interaction: discord.Interaction) -> bool:
            if interaction.user.id in self.banned_users:
                await interaction.response.send_message("⚠️ You are banned from using this bot.", ephemeral=True)
                return False
            return True

        # Load cogs first so their app-commands are registered before syncing
        await self.load_cogs()
        await self.tree.sync()
        self.cleanup_task.start()

        self.db_ping_task.start()
        self.activity_loop.start()
        self.stats_update_task.start()
        self.smart_name_cache_loop.start()
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
    
    def load_banned_users(self):
        """Load banned user IDs from file."""
        from src.config import BANNED_USERS_FILE
        if os.path.exists(BANNED_USERS_FILE):
            try:
                with open(BANNED_USERS_FILE, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line and line.isdigit():
                            self.banned_users.add(int(line))
                print(f"✅ Loaded {len(self.banned_users)} banned user(s).")
            except Exception as e:
                print(f"⚠️ Failed to load banned users: {e}")
        else:
            print(f"📝 No banned users file found ({BANNED_USERS_FILE}).")

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

    @tasks.loop(minutes=30)
    async def cleanup_task(self):
        """Clean up stale games and solo games."""
        now = datetime.datetime.now()
        to_remove = []
        
        for cid, game in self.games.items():
            delta = now - game.start_time
            if delta.total_seconds() > 21600:  # 6 hours
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
            delta = now - game.start_time
            if delta.total_seconds() > 21600:  # 6 hours
                custom_remove.append(cid)

        for cid in custom_remove:
            self.custom_games.pop(cid, None)

        solo_remove = []
        for uid, sgame in self.solo_games.items():
            delta = now - sgame.start_time
            if delta.total_seconds() > 21600:  # 6 hours
                solo_remove.append(uid)

        for uid in solo_remove:
            self.solo_games.pop(uid, None)



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

    @tasks.loop(hours=6)
    async def stats_update_task(self):
        """Update bot stats every 6 hours for the webpage."""
        await self.wait_until_ready()
        try:
            import json
            import os
            
            stats = {
                'server_count': len(self.guilds),
                'simple_words': len(self.secrets),
                'classic_words': len(self.hard_secrets),
                'total_words': len(self.valid_set),
                'last_updated': datetime.datetime.utcnow().isoformat()
            }
            
            # Write to shared file that Flask server can read
            stats_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            stats_file = os.path.join(stats_dir, 'static', 'bot_stats.json')
            
            with open(stats_file, 'w') as f:
                json.dump(stats, f)
                
            print(f"📊 Stats updated: {stats['server_count']} servers, {stats['total_words']} words")
        except Exception as e:
            print(f"⚠️ Stats update failed: {e}")

    @tasks.loop(hours=48)
    async def smart_name_cache_loop(self):
        """
        Background task to cache ALL player names every 48 hours.
        Updates self.name_cache efficiently to avoid rate limits.
        """
        await self.wait_until_ready()
        print("🔄 Starting Smart Name Cache Update...")
        
        try:
            # 1. Fetch all unique user_ids from stats
            # We use 'head=False' to get data
            # To avoid huge payload, we paginate or just grab IDs if possible. 
            # supabase-py max row limit is usually 1000 without range. 
            # We'll fetch in chunks if needed, but for now let's try a large range or loop properly.
            # Actually, let's just fetch all IDs. Assuming < 10k users for now.
            
            all_users = []
            start = 0
            step = 1000
            while True:
                res = self.supabase_client.table('user_stats_v2').select('user_id').range(start, start + step - 1).execute()
                if not res.data:
                    break
                all_users.extend(r['user_id'] for r in res.data)
                if len(res.data) < step:
                    break
                start += step
            
            print(f"   Found {len(all_users)} users to cache.")
            
            # 2. Iterate and Cache
            # We use a separate dict and swap at the end? 
            # User said "delete old cache only when new cache is ready".
            # So yes, build new cache, then update self.name_cache.
            
            new_cache = {}
            count = 0
            
            for uid in all_users:
                try:
                    # Try local guild cache first (FAST, no API call)
                    name = None
                    for guild in self.guilds:
                        mem = guild.get_member(uid)
                        if mem:
                            name = mem.display_name
                            break
                    
                    if not name:
                         # API Call - Check global cache or fetch
                        user = self.get_user(uid)
                        if user:
                            name = user.display_name
                        else:
                            # Strict Rate Limit Handling
                            try:
                                u = await self.fetch_user(uid)
                                name = u.display_name
                                await asyncio.sleep(0.5) # Prevent 429
                            except:
                                name = f"User {uid}" # Fallback
                                
                    new_cache[uid] = name
                    count += 1
                    if count % 100 == 0:
                        print(f"   Cached {count}/{len(all_users)} names...")
                        
                except Exception as ex:
                    print(f"   Error caching user {uid}: {ex}")
            
            # 3. Swap Cache
            self.name_cache = new_cache
            print(f"✅ Smart Name Cache Updated: {len(self.name_cache)} names cached at {datetime.datetime.utcnow()}.")
            
        except Exception as e:
            print(f"❌ Smart Name Cache Failed: {e}")


# Initialize Bot
bot = WordleBot()


# ========= WELCOME MESSAGE EVENT =========
@bot.event
async def on_guild_join(guild):
    """Send a welcome message when the bot joins a new server."""
    # Try to find the best channel to send welcome message
    target_channel = None
    
    # Priority: System channel > first channel with 'general'/'announce' in name > first text channel
    if guild.system_channel and guild.system_channel.permissions_for(guild.me).send_messages:
        target_channel = guild.system_channel
    else:
        for channel in guild.text_channels:
            if channel.permissions_for(guild.me).send_messages:
                name_lower = channel.name.lower()
                if 'general' in name_lower or 'announce' in name_lower or 'welcome' in name_lower:
                    target_channel = channel
                    break
        
        # Fallback: first available text channel
        if not target_channel:
            for channel in guild.text_channels:
                if channel.permissions_for(guild.me).send_messages:
                    target_channel = channel
                    break
    
    if target_channel:
        embed = discord.Embed(
            title="🎉 Thank you for adding Wordle Game Bot!",
            color=discord.Color.green()
        )
        embed.description = (
            "An engaging Wordle experience with various different game modes, level-up system and leaderboards!\n\n"
            "**Getting Started:**\n"
            "• Use `/help` for full command list and how to play\n"
            "• Start a game with `/wordle` (Simple) or `/wordle_classic` (Hard)\n"
            "• Make guesses with `/guess word:xxxxx` or `-g xxxxx`"
        )
        embed.add_field(
            name="📌 Channel Setup",
            value=(
                "The bot can play in **any channel** it has access to.\n"
                "Simply use commands in your desired channel. No additional setup required!\n"
                "*Tip: Create a `#wordle` channel for dedicated games.*"
            ),
            inline=False
        )
        embed.set_footer(text="🔇 This is the only server message • Minimal spam, minimal permissions")
        
        try:
            await target_channel.send(embed=embed)
        except:
            pass  # Silently fail if we can't send


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

