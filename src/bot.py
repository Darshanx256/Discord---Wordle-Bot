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
from src.database import fetch_user_profile_v2, ensure_word_cache, fetch_guild_allowed_channels
from src.utils import EMOJIS, get_badge_emoji

CHANNEL_ACCESS_CACHE_TTL_SECONDS = 300
CHANNEL_ACCESS_CACHE_SWEEP_INTERVAL = 24 * 3600
CHANNEL_ACCESS_INACTIVE_EVICT_SECONDS = 4 * 24 * 3600

# Commands that should be restricted when guild channel setup is configured.
GAMEPLAY_COMMANDS = {
    "wordle", "wordle_classic", "hard_mode", "guess", "race", "show_race",
    "word_rush", "stop_rush", "custom", "stop_game",
}

# Commands that should always work regardless of channel setup.
ALWAYS_ALLOWED_COMMANDS = {
    "help", "about", "profile", "leaderboard", "leaderboard_global",
    "solo", "show_solo", "cancel_solo",
    "message", "ping", "shop",
    "channel_setup",
}

# ========= BOT SETUP =========
class WordleBot(commands.Bot):
    def __init__(self):
        # We need message_content intent for the -g prefix shortcut
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.members = True

        super().__init__(command_prefix=self.get_custom_prefix, intents=intents, max_messages=10)
        self.help_command = None
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
        self.send_integration_update_on_boot = False
        self._boot_notice_dispatched = False
        self.channel_access_cache = {}  # guild_id -> {'channels': set[int], 'configured': bool, 'loaded_at': float, 'last_access': float}
        self.channel_access_locks = {}  # guild_id -> asyncio.Lock

    def _get_interaction_command_name(self, interaction: discord.Interaction) -> str:
        cmd = interaction.command
        cmd_name = getattr(cmd, "name", None)
        if not cmd_name and interaction.data:
            cmd_name = interaction.data.get("name")
        return (cmd_name or "").lower()

    def _touch_channel_access_cache(self, guild_id: int):
        entry = self.channel_access_cache.get(guild_id)
        if entry:
            entry["last_access"] = time.monotonic()

    def _set_channel_access_cache(self, guild_id: int, channels):
        now = time.monotonic()
        configured = channels is not None
        self.channel_access_cache[guild_id] = {
            "channels": set(channels or []),
            "configured": configured,
            "loaded_at": now,
            "last_access": now,
        }

    async def _refresh_channel_access_cache(self, guild_id: int):
        lock = self.channel_access_locks.setdefault(guild_id, asyncio.Lock())
        async with lock:
            channels = await fetch_guild_allowed_channels(self, guild_id)
            self._set_channel_access_cache(guild_id, channels)
            return self.channel_access_cache[guild_id]

    async def _get_channel_access_entry(self, guild_id: int):
        now = time.monotonic()
        entry = self.channel_access_cache.get(guild_id)
        if entry:
            self._touch_channel_access_cache(guild_id)
            # Keep checks fast; refresh stale entries asynchronously.
            age = now - entry["loaded_at"]
            if age > CHANNEL_ACCESS_CACHE_TTL_SECONDS:
                asyncio.create_task(self._refresh_channel_access_cache(guild_id))
            return entry

        # First interaction for this guild: load once from DB.
        return await self._refresh_channel_access_cache(guild_id)

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
        """Global command guard: bans + optional guild channel restrictions for gameplay commands."""
        if interaction.user.id in self.banned_users:
            if not interaction.response.is_done():
                await interaction.response.send_message("⚠️ You are banned from using this bot.", ephemeral=True)
            return False

        if not interaction.guild:
            return True

        cmd_name = self._get_interaction_command_name(interaction)
        if not cmd_name or cmd_name in ALWAYS_ALLOWED_COMMANDS:
            return True
        if cmd_name not in GAMEPLAY_COMMANDS:
            return True

        entry = await self._get_channel_access_entry(interaction.guild.id)
        configured = entry.get("configured", False)
        if not configured:
            return True

        allowed_channels = entry.get("channels", set())
        if interaction.channel_id in allowed_channels:
            return True

        if not interaction.response.is_done():
            await interaction.response.send_message(
                "Checking channel setup... this gameplay command is not enabled here.\n"
                "Use `/channel_setup add #channel` in an allowed admin channel.",
                ephemeral=True
            )
        return False

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
        self._background_tasks['prefetch'] = asyncio.create_task(self.prefetch_task_loop())
        self._background_tasks['channel_access_cache'] = asyncio.create_task(self.channel_access_cache_task_loop())
        
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
                    import traceback
                    traceback.print_exc()
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
        # 1. Standard valid 5-letter words (Clean for generation)
        temp_valid_set = set()
        if os.path.exists(VALID_FILE):
            with open(VALID_FILE, "r", encoding="utf-8") as f:
                temp_valid_set = {w.strip().lower() for w in f if len(w.strip()) == 5}
        
        # 2. 'Wild' 5-letter words (Validation/Guessing Only - Excluded from generation)
        self.rush_wild_set = frozenset()
        temp_rush_wild_set = set()
        if os.path.exists(RUSH_WILD_FILE):
            with open(RUSH_WILD_FILE, "r", encoding="utf-8") as f:
                temp_rush_wild_set = {w.strip().lower() for w in f if len(w.strip()) == 5}
        self.rush_wild_set = frozenset(temp_rush_wild_set)

        # 3. Puzzles pool (6+ letters)
        if os.path.exists(FULL_WORDS):
            with open(FULL_WORDS, "r", encoding="utf-8") as f:
                self.full_dict = frozenset({w.strip().lower() for w in f if len(w.strip()) >= 5})
        else:
            self.full_dict = frozenset()

        if os.path.exists(CLASSIC_FILE):
            with open(CLASSIC_FILE, "r", encoding="utf-8") as f:
                # NOTE: hard_secrets MUST be alphabetically sorted in the file for stable bitset bit-mapping
                self.hard_secrets = [w.strip().lower() for w in f if len(w.strip()) == 5]
        else:
            self.hard_secrets = []

        # Ensure secrets and hard_secrets are also in the clean valid set
        temp_valid_set.update(self.secrets)
        temp_valid_set.update(self.hard_secrets)
        
        # Freezing the sets for performance
        self.valid_set = frozenset(temp_valid_set)
        
        # Combined 5-letter set for general validation
        self.all_valid_5 = frozenset(temp_valid_set | temp_rush_wild_set)
    
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
            response = self.supabase_client.table('user_stats_v2').select('count', count='exact').limit(0).execute()
            
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

                # Clean up Race Sessions
                race_remove = []
                for cid, session in self.race_sessions.items():
                    # Check inactivity based on start_time or last interaction
                    delta = now_dt - session.start_time
                    if delta.total_seconds() > 3600: # 1 hour max for a race lobby/game (Race is 10 mins usually)
                        race_remove.append(cid)
                for cid in race_remove:
                    self.race_sessions.pop(cid, None)

                # Clean up Word Rush (Constraint Mode)
                rush_remove = []
                for cid, game in self.constraint_mode.items():
                    # Use round_start_time if active, else lobby start time?
                    # We can track 'last_interaction' on game object conceptually or just use start time safety net
                    # Rush can be long (100 rounds), give it generous 2 hours timeout if idle
                    delta = time.monotonic() - (game.round_start_time if game.round_start_time else 0)
                    
                    # If game hasn't started (round 0) and it's been > 30 mins (lobby stuck?)
                    is_stuck_lobby = (game.round_number == 0 and delta > 1800)
                    
                    # If game is running but no activity for > 30 mins
                    is_stuck_game = (game.round_number > 0 and delta > 1800)

                    # Simple Fallback: Just clear if very old? 
                    # We accept that run_game_loop SHOULD handle it. This is just garbage collection.
                    # Let's say 2 hours absolute max?
                    
                    if is_stuck_lobby or is_stuck_game:
                         rush_remove.append(cid)
                
                for cid in rush_remove:
                    self.constraint_mode.pop(cid, None)
                
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
                            self.supabase_client.table('user_stats_v2').select('count', count='exact').limit(0).execute()
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
                    # Write to project root (static folder removed)
                    stats_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                    stats_file = os.path.join(stats_dir, 'bot_stats.json')
                    with open(stats_file, 'w') as f:
                        json.dump(stats, f)
                    print(f"📊 Stats updated: {stats['server_count']} servers")
                except Exception as e:
                    print(f"⚠️ Stats update failed: {e}")
                next_run = time.monotonic() + INTERVAL
                
            remaining = next_run - time.monotonic()
            await asyncio.sleep(min(max(remaining, 1), 600))

    async def smart_name_cache_loop_task(self):
        """
        Background task to cache ONLY the Top 10 Global Players (by WR).
        Other users are handled via mentions in V2 logic.
        """
        await self.wait_until_ready()
        INTERVAL = 5 * 60 # 5 minutes
        next_run = time.monotonic()
        
        while not self.is_closed():
            if time.monotonic() >= next_run:
                print("🔄 Starting Smart Name Cache Update...")
                try:
                    # 1. Fetch Top 10 IDs by WR
                    res = await asyncio.to_thread(lambda: self.supabase_client.table('user_stats_v2')
                            .select('user_id')
                            .order('multi_wr', desc=True)
                            .limit(10)
                            .execute())
                    
                    if res.data:
                        top_users = [r['user_id'] for r in res.data]

                        # 2. Refresh Cache ONLY for these VIPs
                        new_cache = {}
                        count = 0
                        for uid in top_users:
                            try:
                                # Safe fetch, rate limited internally by Discord lib but okay for 10 users
                                u = await self.fetch_user(uid)
                                new_cache[uid] = u.display_name
                                count += 1
                            except:
                                pass

                        self.name_cache = new_cache
                        print(f"✅ Top 10 Name Cache Updated: {count} users")
                        
                except Exception as e:
                    print(f"❌ Smart Name Cache Failed: {e}")
                next_run = time.monotonic() + INTERVAL
                
            remaining = next_run - time.monotonic()
            await asyncio.sleep(min(max(remaining, 1), 3600))

    async def prefetch_task_loop(self):
        """One-off background task to prefill word caches for all guilds with semaphored batching."""
        await self.wait_until_ready()
        print(f"🔄 Starting Word Cache Prefetch for {len(self.guilds)} guilds...")
        
        # Limit concurrency to avoid overwhelming the database (Batching logic)
        sem = asyncio.Semaphore(15) 
        
        async def _safe_ensure(guild_id):
            async with sem:
                try:
                    await ensure_word_cache(self, guild_id, wait=True)
                except Exception as e:
                    print(f"⚠️ Prefetch Error for guild {guild_id}: {e}")

        # Dispatch all prefetches
        tasks = [_safe_ensure(guild.id) for guild in self.guilds]
        await asyncio.gather(*tasks)
                
        print(f"✅ Word Cache Prefetch completed for all guilds.")

    async def channel_access_cache_task_loop(self):
        """
        Every 24 hours, evict channel access cache entries that were inactive for 4 days.
        """
        await self.wait_until_ready()
        interval = CHANNEL_ACCESS_CACHE_SWEEP_INTERVAL
        next_run = time.monotonic()

        while not self.is_closed():
            if time.monotonic() >= next_run:
                now = time.monotonic()
                stale = []
                for guild_id, entry in self.channel_access_cache.items():
                    last_access = entry.get("last_access", entry.get("loaded_at", now))
                    if now - last_access >= CHANNEL_ACCESS_INACTIVE_EVICT_SECONDS:
                        stale.append(guild_id)

                for guild_id in stale:
                    self.channel_access_cache.pop(guild_id, None)
                    self.channel_access_locks.pop(guild_id, None)

                if stale:
                    print(f"🧹 Channel access cache evicted {len(stale)} inactive guild entries.")
                next_run = time.monotonic() + interval

            remaining = next_run - time.monotonic()
            await asyncio.sleep(min(max(remaining, 1), 3600))


# Initialize Bot
bot = WordleBot()


def _get_announcement_channel(guild: discord.Guild):
    """Find a suitable channel for optional one-time setup notices."""
    target_channel = None

    if guild.system_channel and guild.system_channel.permissions_for(guild.me).send_messages:
        target_channel = guild.system_channel
    else:
        for channel in guild.text_channels:
            if channel.permissions_for(guild.me).send_messages:
                name_lower = channel.name.lower()
                if 'general' in name_lower or 'announce' in name_lower or 'welcome' in name_lower:
                    target_channel = channel
                    break

        if not target_channel:
            for channel in guild.text_channels:
                if channel.permissions_for(guild.me).send_messages:
                    target_channel = channel
                    break

    return target_channel


def _build_welcome_embed():
    embed = discord.Embed(
        title="🎉 Thank you for adding Wordle Game Bot!",
        color=discord.Color.green()
    )
    embed.description = (
        "An engaging Wordle experience with various different game modes, level-up system and leaderboards!\n\n"
        "**Getting Started:**\n"
        "• Use `/help` for full command list and how to play\n"
        "• Start a game with `/wordle`\n"
        "• Make guesses with `/guess word:xxxxx` or `-g xxxxx`"
    )
    embed.add_field(
        name="📌 Channel Setup (Optional)",
        value=(
            "No setup is required. The bot works in all channels by default.\n"
            "Optional setup methods:\n"
            "• Use `/channel_setup` commands to allow gameplay in specific channels\n"
            "• Or configure Discord Integrations if that menu is available\n"
            "`Server Settings -> Integrations -> Wordle Game Bot`"
        ),
        inline=False
    )
    embed.set_footer(text="🔇 Minimal server messages • setup is optional")
    return embed


def _build_boot_integration_embed():
    embed = discord.Embed(
        title="🧩 Setup Update",
        color=discord.Color.blue()
    )
    embed.description = (
        "A recent update made channel control simpler for large servers.\n\n"
        "Setup is optional. If you skip it, the bot keeps working in all channels.\n\n"
        "If you want restrictions, use:\n"
        "• `/channel_setup add #channel`\n"
        "• `/channel_setup list` and `/channel_setup clear`\n\n"
        "You can also use Discord Integrations if visible:\n"
        "`Server Settings -> Integrations -> Wordle Game Bot`"
    )
    embed.set_footer(text="🔇 One-time reminder for this restart")
    return embed


async def _safe_send_embed(channel: discord.abc.Messageable, embed: discord.Embed, max_attempts: int = 3) -> bool:
    """
    Send helper with explicit retry/backoff for transient Discord HTTP failures.
    discord.py already rate-limits per route; this adds a defensive fallback.
    """
    for attempt in range(max_attempts):
        try:
            await channel.send(embed=embed)
            return True
        except discord.HTTPException as e:
            # Respect retry_after when available, otherwise back off incrementally.
            retry_after = getattr(e, "retry_after", None)
            wait_s = float(retry_after) if retry_after else (1.0 + attempt)
            await asyncio.sleep(min(max(wait_s, 0.5), 10.0))
        except:
            return False
    return False


# ========= WELCOME MESSAGE EVENT =========
@bot.event
async def on_guild_join(guild):
    """Send a welcome message when the bot joins a new server."""
    try:
        await ensure_word_cache(bot, guild.id, wait=True)
    except Exception as e:
        print(f"⚠️ Failed to prefill cache for new guild {guild.id}: {e}")

    target_channel = _get_announcement_channel(guild)
    if target_channel:
        try:
            await target_channel.send(embed=_build_welcome_embed())
        except:
            pass  # Silently fail if we can't send


@bot.event
async def on_ready():
    """Optional one-time startup reminder for integration setup."""
    if not bot.send_integration_update_on_boot or bot._boot_notice_dispatched:
        return

    bot._boot_notice_dispatched = True
    notice_embed = _build_boot_integration_embed()
    sent_count = 0

    print("📣 Sending one-time integration setup reminder to all servers...")
    for guild in bot.guilds:
        target_channel = _get_announcement_channel(guild)
        if not target_channel:
            continue
        try:
            sent = await _safe_send_embed(target_channel, notice_embed, max_attempts=4)
            if sent:
                sent_count += 1
            # Soft pacing to reduce burst pressure in large deployments.
            await asyncio.sleep(0.35)
        except:
            pass
    print(f"✅ Startup reminder sent to {sent_count}/{len(bot.guilds)} servers.")


def _ask_skip_integration_message() -> bool:
    """
    Ask at startup whether to skip the post-boot integration reminder.
    Returns True when reminder should be skipped.
    """
    prompt = "Skip one-time integration setup reminder broadcast after boot? (y/n): "
    try:
        answer = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n⚠️ No interactive input detected. Defaulting to skip reminder.")
        return True

    if answer in {"n", "no"}:
        return False
    return True


# Run bot
def main():
    TOKEN = os.getenv('DISCORD_TOKEN')
    if not TOKEN:
        print("❌ DISCORD_TOKEN not set in environment variables.")
        sys.exit(1)

    skip_notice = _ask_skip_integration_message()
    bot.send_integration_update_on_boot = not skip_notice
    print(f"ℹ️ Integration reminder broadcast: {'enabled' if not skip_notice else 'skipped'}")

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
    
    view = discord.ui.View()
    
    async def buy_duck(inter: discord.Interaction):
        if duck_count >= 4:
            await asyncio.to_thread(lambda: bot.supabase_client.table('user_stats_v2').update({'active_badge': 'duck_lord_badge'}).eq('user_id', interaction.user.id).execute())
            await inter.response.send_message("✅ Equipped Duck Lord Badge!", ephemeral=True)
        else:
            await inter.response.send_message(f"❌ Need 4 Ducks. You have {duck_count}.", ephemeral=True)

    async def buy_dragon(inter: discord.Interaction):
        if dragon_count >= 2:
            await asyncio.to_thread(lambda: bot.supabase_client.table('user_stats_v2').update({'active_badge': 'dragon_slayer_badge'}).eq('user_id', interaction.user.id).execute())
            await inter.response.send_message("✅ Equipped Dragon Slayer Badge!", ephemeral=True)
        else:
            await inter.response.send_message(f"❌ Need 2 Dragons. You have {dragon_count}.", ephemeral=True)

    async def buy_candy(inter: discord.Interaction):
        if candy_count >= 3:
            await asyncio.to_thread(lambda: bot.supabase_client.table('user_stats_v2').update({'active_badge': 'candy_rush_badge'}).eq('user_id', interaction.user.id).execute())
            await inter.response.send_message("✅ Equipped Sugar Rush Badge!", ephemeral=True)
        else:
            await inter.response.send_message(f"❌ Need 3 Candies. You have {candy_count}.", ephemeral=True)

    async def unequip(inter: discord.Interaction):
        current_badge = p.get('active_badge')
        if not current_badge:
            await inter.response.send_message("⚠️ No badge equipped.", ephemeral=True)
        else:
            await asyncio.to_thread(lambda: bot.supabase_client.table('user_stats_v2').update({'active_badge': None}).eq('user_id', interaction.user.id).execute())
            await inter.response.send_message("✅ Badge unequipped.", ephemeral=True)
               
    b1 = discord.ui.Button(label="Duck Lord Badge (4 Ducks)", style=discord.ButtonStyle.secondary, emoji=EMOJIS.get('duck_lord_badge', '🦆'), disabled=(duck_count < 4))
    b1.callback = buy_duck
    
    b2 = discord.ui.Button(label="Dragon Slayer Badge (2 Dragons)", style=discord.ButtonStyle.secondary, emoji=EMOJIS.get('dragon_slayer_badge', '🐲'), disabled=(dragon_count < 2))
    b2.callback = buy_dragon
    
    b3 = discord.ui.Button(label="Sugar Rush Badge (3 Candies)", style=discord.ButtonStyle.secondary, emoji=EMOJIS.get('candy_rush_badge', '🍬'), disabled=(candy_count < 3))
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
