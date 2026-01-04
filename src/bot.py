import os
import sys
import asyncio
import datetime
import random
import time
import discord
from discord.ext import commands, tasks
from supabase import create_client, Client

from src.config import SUPABASE_URL, SUPABASE_KEY, SECRET_FILE, VALID_FILE, FULL_WORDS, CLASSIC_FILE, ROTATING_ACTIVITIES
from src.database import fetch_user_profile_v2
from src.utils import EMOJIS, get_badge_emoji

# ========= BOT SETUP =========
class WordleBot(commands.Bot):
    def __init__(self):
        # We need message_content intent for the -g prefix shortcut
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.members = True

        super().__init__(command_prefix=self.get_custom_prefix, intents=intents, max_messages=10)
        self.games = {}
        self.solo_games = {}
        self.custom_games = {}  # Custom mode games
        self.race_sessions = {}  # Race mode lobbies
        self.constraint_mode = {} # Word Rush
        self.stopped_games = set()
        self.egg_cooldowns = {}
        self.secrets = []
        self.hard_secrets = []
        self.valid_set = set()
        self.full_dict = set()
        self.name_cache = {}
        self.supabase_client: Client = None
        self.banned_users = set()  # Banned user IDs
        self._background_tasks = {} # Name: Task

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

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Global Ban Check for Slash Commands."""
        if interaction.user.id in self.banned_users:
            if not interaction.response.is_done():
                await interaction.response.send_message("⚠️ You are banned from using this bot.", ephemeral=True)
            return False
        return True

    async def setup_hook(self):
        self.load_local_data()
        self.load_banned_users()
        self.setup_db()
        
        # Register Global Ban Check for Slash Commands
        self.tree.interaction_check = self.interaction_check

        # Load cogs first so their app-commands are registered before syncing
        await self.load_cogs()
        await self.tree.sync()
        
        # Start refactored background tasks
        self._background_tasks['cleanup'] = asyncio.create_task(self.cleanup_task_loop())
        self._background_tasks['db_ping'] = asyncio.create_task(self.db_ping_task_loop())
        self._background_tasks['activity'] = asyncio.create_task(self.activity_loop_task())
        self._background_tasks['stats'] = asyncio.create_task(self.stats_update_task_loop())
        self._background_tasks['name_cache'] = asyncio.create_task(self.smart_name_cache_loop_task())
        self._background_tasks['streak_reminder'] = asyncio.create_task(self.streak_reminder_task_loop())
        
        print(f"✅ Ready! {len(self.secrets)} simple secrets, {len(self.hard_secrets)} classic secrets.")

    async def streak_reminder_task_loop(self):
        """Sends DM reminders to users with 3+ day streaks 4 hours before UTC reset."""
        await self.wait_until_ready()
        
        while not self.is_closed():
            now = datetime.datetime.utcnow()
            # 20:00 UTC is 4 hours before 00:00 UTC reset
            if now.hour == 20:
                today_str = now.date().isoformat()
                try:
                    res = self.supabase_client.table('streaks_v4') \
                        .select('user_id, current_streak') \
                        .gte('current_streak', 3) \
                        .lt('last_play_date', today_str) \
                        .execute()
                    
                    if res.data:
                        for row in res.data:
                            uid = row['user_id']
                            streak = row['current_streak']
                            user = self.get_user(uid) or await self.fetch_user(uid)
                            if user:
                                try:
                                    milestones = [7, 14, 28, 50]
                                    next_m = next((m for m in milestones if m > streak), None)
                                    if next_m:
                                        msg = f"🔔 **Don't miss out on your streak!** You're on a **{streak}-day streak**. Only **{next_m - streak}** more days until you unlock the **Day {next_m} Badge**! 🏆"
                                    else:
                                        msg = f"🔔 **Keep the fire burning!** Your **{streak}-day streak** is at risk. Play a quick game of /wordle or /word_rush now to save it! 🔥"
                                    await user.send(msg)
                                    await asyncio.sleep(1) # Safety
                                except: pass
                except Exception as e:
                    print(f"Streak Reminder Task Error: {e}")
                
                await asyncio.sleep(3601) # Skip rest of hour
            else:
                await asyncio.sleep(1800) # Check every 30 mins

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
        # Note: Added from config imports for clarity if needed, but they are already global
        from src.config import SECRET_FILE, VALID_FILE, FULL_WORDS, CLASSIC_FILE, RUSH_WILD_FILE

        if os.path.exists(SECRET_FILE):
            with open(SECRET_FILE, "r", encoding="utf-8") as f:
                # NOTE: secrets MUST be alphabetically sorted in the file for stable bitset bit-mapping
                self.secrets = [w.strip().lower() for w in f if len(w.strip()) == 5]
        else:
            self.secrets = []

        # 1. Standard valid 5-letter words (Clean for generation)
        self.valid_set = set()
        if os.path.exists(VALID_FILE):
            with open(VALID_FILE, "r", encoding="utf-8") as f:
                self.valid_set = {w.strip().lower() for w in f if len(w.strip()) == 5}
        
        # 2. 'Wild' 5-letter words (Validation/Guessing Only - Excluded from generation)
        self.rush_wild_set = set()
        if os.path.exists(RUSH_WILD_FILE):
            with open(RUSH_WILD_FILE, "r", encoding="utf-8") as f:
                self.rush_wild_set = {w.strip().lower() for w in f if len(w.strip()) == 5}

        # Combined 5-letter set for general validation
        self.all_valid_5 = self.valid_set | self.rush_wild_set

        # 3. Puzzles pool (6+ letters)
        if os.path.exists(FULL_WORDS):
            with open(FULL_WORDS, "r", encoding="utf-8") as f:
                self.full_dict = {w.strip().lower() for w in f if len(w.strip()) >= 5}
        else:
            self.full_dict = set()

        if os.path.exists(CLASSIC_FILE):
            with open(CLASSIC_FILE, "r", encoding="utf-8") as f:
                # NOTE: hard_secrets MUST be alphabetically sorted in the file for stable bitset bit-mapping
                self.hard_secrets = [w.strip().lower() for w in f if len(w.strip()) == 5]
        else:
            self.hard_secrets = []

        # Ensure secrets and hard_secrets are also in the clean valid set
        self.valid_set.update(self.secrets)
        self.valid_set.update(self.hard_secrets)
        
        # Ensure they are in the all-inclusive validation set too
        self.all_valid_5.update(self.valid_set)
    
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

    async def activity_loop_task(self):
        """Rotates the bot's activity status every hour."""
        await self.wait_until_ready()
        INTERVAL = 3600 # 1 hour
        next_run = time.monotonic()
        
        while not self.is_closed():
            if time.monotonic() >= next_run:
                if ROTATING_ACTIVITIES:
                    act_data = random.choice(ROTATING_ACTIVITIES)
                    activity = discord.Activity(type=act_data["type"], name=act_data["name"])
                    await self.change_presence(activity=activity)
                next_run = time.monotonic() + INTERVAL
            
            # Dynamic sleep
            remaining = next_run - time.monotonic()
            await asyncio.sleep(min(max(remaining, 1), 60))

    async def cleanup_task_loop(self):
        """Clean up stale games and solo games."""
        await self.wait_until_ready()
        INTERVAL = 1800 # 30 mins
        next_run = time.monotonic()
        
        while not self.is_closed():
            if time.monotonic() >= next_run:
                now_dt = datetime.datetime.now()
                to_remove = []
                
                for cid, game in self.games.items():
                    delta = now_dt - game.start_time
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
                                await asyncio.sleep(0.5)
                        except:
                            pass

                for cid in to_remove:
                    self.games.pop(cid, None)

                # Clean up custom games
                custom_remove = []
                for cid, game in self.custom_games.items():
                    delta = now_dt - game.start_time
                    if delta.total_seconds() > 21600:  # 6 hours
                        custom_remove.append(cid)
                for cid in custom_remove:
                    self.custom_games.pop(cid, None)

                solo_remove = []
                for uid, sgame in self.solo_games.items():
                    delta = now_dt - sgame.start_time
                    if delta.total_seconds() > 21600:  # 6 hours
                        solo_remove.append(uid)
                for uid in solo_remove:
                    self.solo_games.pop(uid, None)
                
                next_run = time.monotonic() + INTERVAL
            
            remaining = next_run - time.monotonic()
            await asyncio.sleep(min(max(remaining, 1), 60))

    async def db_ping_task_loop(self):
        """Ping Supabase every 4 days to prevent project freeze."""
        await self.wait_until_ready()
        INTERVAL = 96 * 3600 # 4 days
        next_run = time.monotonic()
        
        while not self.is_closed():
            if time.monotonic() >= next_run:
                if self.supabase_client:
                    try:
                        def ping_db_sync():
                            self.supabase_client.table('scores').select('count', count='exact').limit(0).execute()
                        await asyncio.to_thread(ping_db_sync)
                        print(f"✅ DB Ping Task: Successfully pinged Supabase at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                    except Exception as e:
                        print(f"⚠️ DB Ping Task Failed: {e}")
                next_run = time.monotonic() + INTERVAL
            
            remaining = next_run - time.monotonic()
            await asyncio.sleep(min(max(remaining, 1), 3600))

    async def stats_update_task_loop(self):
        """Update bot stats every 6 hours for the webpage."""
        await self.wait_until_ready()
        INTERVAL = 6 * 3600 # 6 hours
        next_run = time.monotonic()
        
        while not self.is_closed():
            if time.monotonic() >= next_run:
                try:
                    import json
                    stats = {
                        'server_count': len(self.guilds),
                        'simple_words': len(self.secrets),
                        'classic_words': len(self.hard_secrets),
                        'total_words': len(self.valid_set),
                        'last_updated': datetime.datetime.utcnow().isoformat()
                    }
                    stats_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                    stats_file = os.path.join(stats_dir, 'static', 'bot_stats.json')
                    with open(stats_file, 'w') as f:
                        json.dump(stats, f)
                    print(f"📊 Stats updated: {stats['server_count']} servers")
                except Exception as e:
                    print(f"⚠️ Stats update failed: {e}")
                next_run = time.monotonic() + INTERVAL
                
            remaining = next_run - time.monotonic()
            await asyncio.sleep(min(max(remaining, 1), 600))

    async def smart_name_cache_loop_task(self):
        """Background task to cache ALL player names every 48 hours."""
        await self.wait_until_ready()
        INTERVAL = 48 * 3600 # 48 hours
        next_run = time.monotonic()
        
        while not self.is_closed():
            if time.monotonic() >= next_run:
                print("🔄 Starting Smart Name Cache Update...")
                try:
                    all_users = []
                    start = 0
                    step = 1000
                    while True:
                        res = self.supabase_client.table('user_stats_v2').select('user_id').range(start, start + step - 1).execute()
                        if not res.data: break
                        all_users.extend(r['user_id'] for r in res.data)
                        if len(res.data) < step: break
                        start += step
                    
                    new_cache = {}
                    for uid in all_users:
                        try:
                            name = None
                            for guild in self.guilds:
                                mem = guild.get_member(uid)
                                if mem:
                                    name = mem.display_name
                                    break
                            if not name:
                                user = self.get_user(uid)
                                if user: name = user.display_name
                                else:
                                    u = await self.fetch_user(uid)
                                    name = u.display_name
                                    await asyncio.sleep(0.5)
                            new_cache[uid] = name
                        except: pass
                    self.name_cache = new_cache
                    print(f"✅ Smart Name Cache Updated: {len(self.name_cache)} names")
                except Exception as e:
                    print(f"❌ Smart Name Cache Failed: {e}")
                next_run = time.monotonic() + INTERVAL
                
            remaining = next_run - time.monotonic()
            await asyncio.sleep(min(max(remaining, 1), 3600))


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

    # Fetch inventory
    user_id = interaction.user.id
    
    try:
        res = bot.supabase_client.table('user_stats_v2').select('eggs, active_badge').eq('user_id', user_id).execute()
    except:
        return await interaction.response.send_message("❌ Database error.", ephemeral=True)
        
    inventory = res.data[0]['eggs'] if res.data and res.data[0]['eggs'] else {}
    active_badge = res.data[0]['active_badge'] if res.data else None
    
    duck_count = inventory.get('duck', 0)
    dragon_count = inventory.get('dragon', 0)
    candy_count = inventory.get('candy', 0)
    
    # Streak Badges ownership check
    has_7_streak = inventory.get('7_streak', 0) > 0
    has_14_streak = inventory.get('14_streak', 0) > 0
    has_28_streak = inventory.get('28_streak', 0) > 0
    has_dragon_badge = inventory.get('dragon_badge', 0) > 0

    embed = discord.Embed(title="🛒 Collection Shop", description="Equip badges based on your findings!", color=discord.Color.gold())
    duck_emoji = EMOJIS.get("duck", "🦆")
    dragon_emoji = EMOJIS.get("dragon", "🐲")
    candy_emoji = EMOJIS.get("candy", "🍬")
    
    inventory_text = f"{duck_emoji} Ducks: {duck_count}\n{dragon_emoji} Dragons: {dragon_count}\n{candy_emoji} Candies: {candy_count}"
    if has_7_streak: inventory_text += f"\n{get_badge_emoji('7_streak')} 7-Streak Badge"
    if has_14_streak: inventory_text += f"\n{get_badge_emoji('14_streak')} 14-Streak Badge"
    if has_28_streak: inventory_text += f"\n{get_badge_emoji('28_streak')} 28-Streak Badge"
    if has_dragon_badge: inventory_text += f"\n{get_badge_emoji('dragon_badge')} Dragon Badge"
    
    embed.add_field(name="Your Inventory", value=inventory_text, inline=False)
    
    if active_badge:
        embed.add_field(name="Equipped", value=f"{get_badge_emoji(active_badge)}", inline=False)

    view = discord.ui.View()
    
    async def equip_callback(interaction: discord.Interaction, badge_id: str, cost_type: str = None, cost_amount: int = 0):
        # Check ownership interaction.user.id again to be safe
        # Determine if we need to BUY or just EQUIP
        # If cost > 0, we check if we OWN it first?
        # Shop Logic: usually "Buy to Equip". If already own, just equip?
        # Existing logic was simple "Pay to Equip".
        # We need to support "Equip if owned".
        
        nonlocal inventory # ref to outer scope 
        
        # Re-fetch to prevent race cond? minimal needed for this scale
        # We will trust the button Logic context
        
        # Logic: If cost_type provided, check balance.
        # However, prompt says "Streak badges... visible to those who win".
        # Streak badges are in inventory (set by streaks.py for free).
        # So for streak badges, cost is 0.
        
        can_equip = False
        msg = ""
        
        if cost_type:
            current_qty = inventory.get(cost_type, 0)
            if current_qty >= cost_amount:
                 # DEDUCT COST? "Exchange collected items".
                 # If it's an exchange, we lose items?
                 # Existing logic implied "Exchange".
                 # "Exchange 3 ducks for Duck Lord".
                 # Assuming "Exchange" = Deduct.
                 
                 # But if I already bought it?
                 # "Make /shop UI good ... not like one applies duck_lord ... and loses on 28 streak badge".
                 # This implies badges should be PERMANENT UNLOCKS usually?
                 # But the current DB schema 'eggs' is just counts of items.
                 # There isn't an 'unlocked_badges' list.
                 # So "Buying" Duck Lord effectively "consumes" ducks to set Active Badge.
                 # If I switch to Streak Badge, do I lose Duck Lord?
                 # If I don't store "Unlocked Duck Lord" state, then YES, I lose it.
                 # To fix "Conflict Resolution", I need to store "Unlocked Badges".
                 # OR, I just don't deduct cost if already unlocked?
                 # But I don't know if unlocked.
                 
                 # User Request Breakdown: "conflict resolution... prevent losing a streak badge by equipping an Easter egg badge".
                 # Streak badges are permanent (in inventory).
                 # Easter egg badges (Duck Lord) are currently configured as "Consumables" (Exchange X ducks).
                 # If I equip Duck Lord, I spend Ducks.
                 # If I equip Streak, I spend nothing (just check inventory).
                 # If I go BACK to Duck Lord, I have to spend Ducks AGAIN?
                 # The user likely wants to SWITCH without penalty if possible, OR just accept that Consumables are Consumables.
                 # "streak ... should only be visible to those who win them" -> Stored in inventory.
                 
                 # Implementation Decision:
                 # 1. Stroke Badges: Equip if in inventory.
                 # 2. Shop Badges: Consumable exchange (Legacy behavior, unless I add 'unlocked_badges' column).
                 # Given constraints, I will keep Shop Badges as Exchange (Consumable), but make sure equipping Streak doesn't DELETE the streak badge from inventory.
                 # Streaks are in 'eggs', keys: '7_streak', etc. Value: 1.
                 # We should NOT deduct streak badges when equipping.
                 
                 if cost_type in ['7_streak', '14_streak', '28_streak', 'dragon_badge']:
                     # Just check existence, don't deduct
                     can_equip = True
                     msg = f"Equipped {get_badge_emoji(badge_id)}!"
                 else:
                     # Deduct consumable
                     inventory[cost_type] -= cost_amount
                     # Update DB inventory
                     bot.supabase_client.table('user_stats_v2').update({'eggs': inventory}).eq('user_id', user_id).execute()
                     can_equip = True
                     msg = f"Exchanged {cost_amount} {EMOJIS.get(cost_type, cost_type)} for {get_badge_emoji(badge_id)}!"
            else:
                msg = f"Not enough {EMOJIS.get(cost_type, cost_type)}!"
        else:
             # Free equip?
             can_equip = True
             msg = f"Equipped {get_badge_emoji(badge_id)}!"

        if can_equip:
             bot.supabase_client.table('user_stats_v2').update({'active_badge': badge_id}).eq('user_id', user_id).execute()
             await interaction.response.send_message(msg, ephemeral=True)
        else:
             await interaction.response.send_message(msg, ephemeral=True)

    # Buttons
    # 1. Duck Lord
    b_duck = discord.ui.Button(label="Duck Lord (3 🦆)", style=discord.ButtonStyle.secondary, emoji=EMOJIS.get('duck_lord_badge', '🦆'))
    b_duck.callback = lambda i: equip_callback(i, 'duck_lord_badge', 'duck', 3)
    view.add_item(b_duck)

    # 2. Dragon Slayer
    b_dragon = discord.ui.Button(label="Dragon Slayer (2 🐲)", style=discord.ButtonStyle.secondary, emoji=EMOJIS.get('dragon_slayer_badge', '🗡️'))
    b_dragon.callback = lambda i: equip_callback(i, 'dragon_slayer_badge', 'dragon', 2)
    view.add_item(b_dragon)
    
    # 3. Candy Wrapper
    b_candy = discord.ui.Button(label="Candy Wrapper (5 🍬)", style=discord.ButtonStyle.secondary, emoji=EMOJIS.get('candy_wrapper_badge', '🍬'))
    b_candy.callback = lambda i: equip_callback(i, 'candy_wrapper_badge', 'candy', 5)
    view.add_item(b_candy)
    
    # Streak Buttons (Visible only if owned!)
    if has_7_streak:
        b_7 = discord.ui.Button(label="Equip 7-Streak", style=discord.ButtonStyle.success, emoji=get_badge_emoji('7_streak'))
        b_7.callback = lambda i: equip_callback(i, '7_streak', '7_streak', 0) # Cost type check checks existence, 0 cost
        view.add_item(b_7)
        
    if has_14_streak:
        b_14 = discord.ui.Button(label="Equip 14-Streak", style=discord.ButtonStyle.success, emoji=get_badge_emoji('14_streak'))
        b_14.callback = lambda i: equip_callback(i, '14_streak', '14_streak', 0)
        view.add_item(b_14)
        
    if has_28_streak:
        b_28 = discord.ui.Button(label="Equip 28-Streak", style=discord.ButtonStyle.success, emoji=get_badge_emoji('28_streak'))
        b_28.callback = lambda i: equip_callback(i, '28_streak', '28_streak', 0)
        view.add_item(b_28)

    if has_dragon_badge:
        b_dr = discord.ui.Button(label="Equip Dragon Badge", style=discord.ButtonStyle.danger, emoji=get_badge_emoji('dragon_badge'))
        b_dr.callback = lambda i: equip_callback(i, 'dragon_badge', 'dragon_badge', 0)
        view.add_item(b_dr)

    await interaction.followup.send(embed=embed, view=view, ephemeral=True)
