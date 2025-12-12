import os
import sys
import asyncio
import datetime
import random
import discord
from discord.ext import commands, tasks
from supabase import create_client, Client

from src.config import SUPABASE_URL, SUPABASE_KEY, SECRET_FILE, VALID_FILE, CLASSIC_FILE, ROTATING_ACTIVITIES
from src.utils import calculate_score, get_win_flavor, get_tier_display
from src.database import (
    update_leaderboard, 
    get_next_secret, 
    get_next_classic_secret, 
    fetch_profile_stats_sync
)
from src.game import WordleGame
from src.ui import LeaderboardView, HelpView, get_markdown_keypad_status

# ========= 5. BOT SETUP =========
class WordleBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents(guilds=True))
        self.games = {} 
        self.secrets = []       
        self.hard_secrets = []  
        self.valid_set = set()  # Full dictionary (Set, for O(1) validation) 
        self.supabase_client: Client = None

    async def setup_hook(self):
        self.load_local_data()
        self.setup_db()
        await self.tree.sync()
        self.cleanup_task.start()
        self.db_ping_task.start()
        self.activity_loop.start() # Start rotating activities
        print(f"✅ Ready! {len(self.secrets)} simple secrets, {len(self.hard_secrets)} classic secrets.")
        
    async def close(self):
        # The Supabase client connection is stateless, no need for explicit closing like a pool
        await super().close()

    def load_local_data(self):
        # Load Simple Secrets (words.txt)
        if os.path.exists(SECRET_FILE):
            with open(SECRET_FILE, "r", encoding="utf-8") as f:
                self.secrets = [w.strip().lower() for w in f if len(w.strip()) == 5]
        else: self.secrets = []

        # Load Classic Secrets (words_hard.txt)
        if os.path.exists(CLASSIC_FILE):
            with open(CLASSIC_FILE, "r", encoding="utf-8") as f:
                self.hard_secrets = [w.strip().lower() for w in f if len(w.strip()) == 5]
        else: self.hard_secrets = []
            
        # Load Full Dictionary (all_words.txt)
        if os.path.exists(VALID_FILE):
            with open(VALID_FILE, "r", encoding="utf-8") as f:
                # Use set comprehension for O(1) lookups
                self.valid_set = {w.strip().lower() for w in f if len(w.strip()) == 5} 
        else: 
            self.valid_set = set()
            
        # Ensure the simple and hard secrets are also valid guesses
        self.valid_set.update(self.secrets) 
        self.valid_set.update(self.hard_secrets)

    def setup_db(self):
        print("Connecting to Supabase client...")
        try:
            # Initialize the Supabase Client
            self.supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
            
            # Simple check to confirm connectivity (e.g., fetching schema name)
            response = self.supabase_client.from_('scores').select('count', count='exact').limit(0).execute()
            
            if response.data is not None:
                print("✅ Supabase client ready and tables accessible.")
            else:
                 # This path usually indicates a connection or RLS issue
                 raise Exception("Failed to confirm Supabase table access.")
                
        except Exception as e:
            print(f"❌ FATAL DB ERROR during Supabase setup: General Error. Details: {e}")
            sys.exit(1) 

    @tasks.loop(minutes=60)
    async def activity_loop(self):
        """Rotates the bot's activity status every hour."""
        await self.wait_until_ready() # Wait for connection before setting presence
        if ROTATING_ACTIVITIES:
            act_data = random.choice(ROTATING_ACTIVITIES)

            # Create Activity object from dict data
            activity = discord.Activity(type=act_data["type"], name=act_data["name"])
            await self.change_presence(activity=activity)

    @tasks.loop(minutes=60)
    async def cleanup_task(self):
        now = datetime.datetime.now()
        to_remove = []
        for cid, game in self.games.items():
            delta = now - game.last_interaction
            if delta.total_seconds() > 86400: # 24 Hours
                to_remove.append(cid)
                try:
                    channel = self.get_channel(cid)
                    if channel:
                        embed = discord.Embed(title="⏰ Time's Up!", description=f"Game timed out.\nThe word was **{game.secret.upper()}**.", color=discord.Color.dark_grey())
                        await channel.send(embed=embed)
                        # --- FIX FOR RATE LIMIT ---
                        await asyncio.sleep(1) # Wait 1 second between sending cleanup messages
                        # --------------------------
                except:
                    pass
        
        for cid in to_remove:
            self.games.pop(cid, None)
    
    @tasks.loop(hours=96) # Pinging database once every 4 day to prevent freeze
    async def db_ping_task(self):
        """Pings the Supabase database with a low-impact query to prevent project pausing."""
        await self.wait_until_ready()
        # Using a simple COUNT is the lowest load operation.
        if self.supabase_client:
            try:
                def ping_db_sync():
                    self.supabase_client.table('scores') \
                        .select('count', count='exact') \
                        .limit(0) \
                        .execute()
                
                # Execute the synchronous Supabase call in a separate thread
                await asyncio.to_thread(ping_db_sync)
                print(f"✅ DB Ping Task: Successfully pinged Supabase at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

            except Exception as e:
                print(f"⚠️ DB Ping Task Failed: {e}")


# Initialize Bot
bot = WordleBot()

# ========= 6. EVENTS & COMMANDS =========

# Helper function 
async def fetch_and_format_rankings(results, bot_instance, guild=None):
    # OPTIMIZATION: Concurrency with Rate Limiting
    # fetching users can be slow. We use a semaphore to limit concurrent requests
    # to 5 at a time to be safe against rate limits while speeding up the overall process.
    sem = asyncio.Semaphore(5) 

    async def fetch_user_safe(row_data):
        i, (uid, w, g, s) = row_data
        name = f"User {uid}"
        
        # 1. Try Local Cache (FAST & SAFE)
        if guild:
            member = guild.get_member(uid)
            if member:
                return (i + 1, member.display_name, w, g, (w/g)*100 if g > 0 else 0, s)
        
        # 2. Try Global Bot Cache (FAST & SAFE)
        user = bot_instance.get_user(uid)
        if user:
            return (i + 1, user.display_name, w, g, (w/g)*100 if g > 0 else 0, s)

        # 3. API Call (SLOW - Needs Semaphore)
        async with sem:
            try:
                u = await bot_instance.fetch_user(uid)
                name = u.display_name
            except:
                pass # Name stays "User {uid}" if fetch fails
        
        return (i + 1, name, w, g, (w/g)*100 if g > 0 else 0, s)

    # Launch all tasks
    tasks = [fetch_user_safe((i, r)) for i, r in enumerate(results)]
    
    # Wait for all to complete (asyncio.gather returns results in the order of tasks)
    formatted_data = await asyncio.gather(*tasks)
    return formatted_data


@bot.tree.command(name="help", description="How to play and command guide.")
async def help_command(interaction: discord.Interaction):
    # Uses the advanced HelpView with pages
    view = HelpView(interaction.user)
    # Using ephemeral=True so it doesn't clutter chat, or False if preferred. 
    # Usually users prefer help to be visible to them only or public. 
    # Let's make it public so others can see "show more" too if they want, 
    # but interaction check restricts buttons to "interaction.user". 
    # Actually, ephemeral is safer for personal help. 
    await interaction.response.send_message(embed=view.create_embed(), view=view, ephemeral=True)


@bot.tree.command(name="wordle", description="Start a new game (Simple word list).")
async def start(interaction: discord.Interaction):
    if not interaction.guild: return await interaction.response.send_message("❌ Command must be used in a server.", ephemeral=True)
    
    if not bot.secrets:
        return await interaction.response.send_message("❌ Simple word list missing.", ephemeral=True)

    cid = interaction.channel_id
    if cid in bot.games:
        await interaction.response.send_message("⚠️ Game already active. Use `/stop_game` to end it.", ephemeral=True)
        return
        
    # Get secret from the simple pool
    secret = get_next_secret(bot, interaction.guild_id)
    
    # Easter Egg Title
    title = "✨ Wordle Started! (Simple)"
    if random.randint(1, 100) == 1:
        title = "🪄 Wordle Started! (Magical Edition)"
    
    embed = discord.Embed(title=title, color=discord.Color.blue())
    embed.description = "A simple **5-letter word** has been chosen. **6 attempts** total."
    embed.add_field(name="How to Play", value="`/guess word:xxxxx`", inline=False)
    
    await interaction.response.send_message(embed=embed)
    
    msg = await interaction.original_response()
    bot.games[cid] = WordleGame(secret, cid, interaction.user, msg.id)


@bot.tree.command(name="wordle_classic", description="Start a Classic game (Full dictionary list).")
async def start_classic(interaction: discord.Interaction):
    if not interaction.guild: return await interaction.response.send_message("❌ Command must be used in a server.", ephemeral=True)
    
    if not bot.hard_secrets:
        return await interaction.response.send_message("❌ Classic word list missing.", ephemeral=True)

    cid = interaction.channel_id
    
    if cid in bot.games:
        await interaction.response.send_message("⚠️ Game already active. Use `/stop_game` to end it.", ephemeral=True)
        return
    # Get secret from the full classic pool
    secret = get_next_classic_secret(bot, interaction.guild_id)
    
    # Easter Egg Title (Classic)
    title = "⚔️ Wordle Started! (Classic)"
    if random.randint(1, 100) == 1:
        title = "🐲 Wordle Started! (Dragon Slayer Mode)"
        
    embed = discord.Embed(title=title, color=discord.Color.dark_gold())
    embed.description = "A **word from the full dictionary** has been chosen. **6 attempts** total. Harder than Simple mode!"
    embed.add_field(name="How to Play", value="`/guess word:xxxxx`", inline=False)
    
    await interaction.response.send_message(embed=embed)
    
    msg = await interaction.original_response()
    bot.games[cid] = WordleGame(secret, cid, interaction.user, msg.id)

@bot.tree.command(name="stop_game", description="Force stop the current game (For Starter or Admin only).")
async def stop_game(interaction: discord.Interaction):
    cid = interaction.channel_id
    game = bot.games.get(cid)
    
    if not game:
        return await interaction.response.send_message("No active game to stop.", ephemeral=True)
    
    is_starter = interaction.user.id == game.started_by.id
    is_admin = interaction.permissions.manage_messages
    
    if is_starter or is_admin:
        bot.games.pop(cid)
        await interaction.response.send_message(f"🛑 Game stopped by {interaction.user.mention}. The word was **{game.secret.upper()}**.")
    else:
        await interaction.response.send_message("❌ Only the player who started the game or an Admin can stop it.", ephemeral=True)

@bot.tree.command(name="guess", description="Guess a 5-letter word.")
async def guess(interaction: discord.Interaction, word: str):
    # 🚨 CRITICAL FIX: Acknowledge the interaction immediately to prevent the 3-second timeout
    await interaction.response.defer() 
    
    if not interaction.guild: 
        return await interaction.followup.send("Error: Command must be used in a guild.", ephemeral=True) 
    
    cid = interaction.channel_id
    g_word = word.lower().strip()
    game = bot.games.get(cid)

    if not game:
        return await interaction.followup.send("⚠️ No active game. Start with `/wordle` or `/wordle_classic`.", ephemeral=True)

    if game.is_duplicate(g_word):
        return await interaction.followup.send(f"⚠️ **{g_word.upper()}** was already guessed!", ephemeral=True)

    if len(g_word) != 5 or not g_word.isalpha():
        return await interaction.followup.send("⚠️ 5 letters only.", ephemeral=True)
    if g_word not in bot.valid_set:
        return await interaction.followup.send(f"⚠️ **{g_word.upper()}** not in dictionary.", ephemeral=True)

    # --- Game Turn Logic ---
    pattern, win, game_over = game.process_turn(g_word, interaction.user)
    
    # Get the full, verbose keypad status
    keypad = get_markdown_keypad_status(game.used_letters)
    
    # Progress Bar Logic (simple emojis)
    filled = "●" * game.attempts_used
    empty = "○" * (6 - game.attempts_used)
    progress_bar = f"[{filled}{empty}]"

    # Board Display (using only the emoji pattern)
    board_display = "\n".join([f"{h['pattern']}" for h in game.history])
    
    # --- HINT SYSTEM (Using new emoji suffix check) ---
    hint_msg = ""
    if game.attempts_used == 3:
        # Check for custom emoji color suffixes (e.g., "_green" in the pattern string)
        all_gray = all(
            "green_" not in x['pattern'] and "yellow_" not in x['pattern'] 
            for x in game.history
        )
        
        if all_gray:
            known_absent = game.used_letters['absent']
            available_letters = [c for c in game.secret if c.lower() not in known_absent]

            if available_letters:
                hint_letter = random.choice(available_letters).upper()
                hint_msg = f"\n\n**💡 HINT!** The letter **{hint_letter}** is in the word."
    # --- END HINT SYSTEM ---
    
    # Construct the message content to hold the long keypad status (2000 char limit)
    message_content = f"⌨️ **Keyboard Status:**\n{keypad}"
    
    if win:
        flavor = get_win_flavor(game.attempts_used)
        embed = discord.Embed(title=f"🏆 VICTORY!\n{flavor}", color=discord.Color.green())
        embed.description = f"**{interaction.user.mention}** found the word: **{game.secret.upper()}** in {game.attempts_used}/6 attempts!"
        embed.add_field(name="Final Board", value=board_display, inline=False)
    elif game_over:
        embed = discord.Embed(title="💀 GAME OVER", color=discord.Color.red())
        embed.description = f"The word was **{game.secret.upper()}**."
        embed.add_field(name="Final Board", value=board_display, inline=False)
    else:
        embed = discord.Embed(title=f"Attempt {game.attempts_used}/6", color=discord.Color.gold())
        embed.description = f"**{interaction.user.display_name}** guessed: `{g_word.upper()}`"
        embed.add_field(name="Current Board", value=board_display + hint_msg, inline=False)
        embed.set_footer(text=f"{6 - game.attempts_used} tries left {progress_bar}")
        
    await interaction.followup.send(content=message_content, embed=embed)
    
    if game_over or win:
        winner_id = interaction.user.id if win else None
        # The update_leaderboard call is synchronous, running it in a thread to prevent blocking
        for pid in game.participants:
            # Use asyncio.to_thread for robust non-blocking execution
            await asyncio.to_thread(update_leaderboard, bot, pid, interaction.guild_id, (pid == winner_id))
        bot.games.pop(cid, None)

@bot.tree.command(name="wordle_board", description="View current board.")
async def board(interaction: discord.Interaction):
    if not interaction.guild: return
    game = bot.games.get(interaction.channel_id)
    if not game: return await interaction.response.send_message("❌ No active game.", ephemeral=True)
    
    if not game.history:
        board_display = "No guesses yet! Start guessing with `/guess`."
    else:
        board_display = "\n".join([f"{h['pattern']}" for h in game.history]) 

    embed = discord.Embed(title="📊 Current Board", description=board_display, color=discord.Color.blurple())
    embed.add_field(name="Keyboard Status", value=get_markdown_keypad_status(game.used_letters), inline=False)
    embed.set_footer(text=f"Attempts: {game.attempts_used}/6")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="leaderboard", description="Server Leaderboard.")
async def leaderboard(interaction: discord.Interaction):
    if not interaction.guild: return
    
    await interaction.response.defer()
    uid = interaction.user.id
    guild_id = interaction.guild_id

    try:
        response = bot.supabase_client.table('guild_leaderboard') \
            .select('user_id, guild_wins, guild_games, guild_score, guild_rank') \
            .eq('guild_id', guild_id) \
            .order('guild_rank', desc=False) \
            .execute()
            
        scored_results = []
        user_rank = None
        
        for row in response.data:
            user_id = row['user_id']
            scored_results.append((
                user_id,
                row['guild_wins'],
                row['guild_games'],
                round(row['guild_score'], 2)
            ))
            
            if user_id == uid:
                user_rank = row['guild_rank']

    except Exception as e:
        return await interaction.followup.send("❌ Database error retrieving leaderboard.", ephemeral=True)

    if not scored_results:
        return await interaction.followup.send("No games played yet!", ephemeral=True)

    data = await fetch_and_format_rankings(scored_results, bot, interaction.guild)
    
    footer_text = f"🏆 {interaction.guild.name} Leaderboard"
    if user_rank is not None:
        footer_text += f" | Your Rank: #{user_rank}"

    view = LeaderboardView(
        bot, 
        data, 
        footer_text,
        discord.Color.gold(), 
        interaction.user
    )
    
    await interaction.followup.send(embed=view.create_embed(), view=view)


@bot.tree.command(name="leaderboard_global", description="Global Leaderboard.")
async def leaderboard_global(interaction: discord.Interaction):
    if not interaction.response.is_done():
        try:
            await interaction.response.defer()
        except discord.errors.NotFound:
            print("Interaction already timed out or processed elsewhere.")
            return

    uid = interaction.user.id
    
    try:
        # 1. FETCH DATA FROM MATERIALIZED VIEW
        response = bot.supabase_client.table('global_leaderboard') \
            .select('user_id, global_wins, global_games, global_score, global_rank') \
            .order('global_rank', desc=False) \
            .execute()
            
        scored_results = []
        user_rank = None
        
        for row in response.data:
            user_id = row['user_id']
            # Map columns to the expected order: (uid, wins, games, score)
            scored_results.append((
                user_id,
                row['global_wins'],
                row['global_games'],
                round(row['global_score'], 2) # Rounding to 2 decimals
            ))
            
            if user_id == uid:
                user_rank = row['global_rank']
        
    except Exception as e:
        return await interaction.followup.send("❌ Database error retrieving global leaderboard.", ephemeral=True)

    if not scored_results:
        return await interaction.followup.send("No global games yet!", ephemeral=True)

    # 2. FORMATTING AND DISPLAY
    data = await fetch_and_format_rankings(scored_results, bot)
    
    footer_text = "Global Leaderboard"
    if user_rank is not None:
        footer_text += f" | Your Rank: #{user_rank}"

    view = LeaderboardView(
        bot, 
        data, 
        footer_text, 
        discord.Color.purple(), 
        interaction.user
    )
    
    await interaction.followup.send(embed=view.create_embed(), view=view)


@bot.tree.command(name="profile", description="Check your personal stats.")
async def profile(interaction: discord.Interaction):
    if not interaction.guild: return
    uid = interaction.user.id
    
    await interaction.response.defer()
    
    try:
        # Use simple partial to pass arguments to the synchronous function
        # Or just use key arguments in a lambda, but asyncio.to_thread supports args
        stats = await asyncio.to_thread(fetch_profile_stats_sync, bot, uid, interaction.guild_id)
        
        (s_wins, s_games, s_score, s_tier_icon, s_tier_name, s_rank_num,
         g_wins, g_games, g_score, g_tier_icon, g_tier_name, g_rank_num) = stats

    except Exception as e:
        print(f"DB ERROR in profile: {e}")
        return await interaction.followup.send("❌ Database error retrieving your profile.", ephemeral=True)

    # --- BEAUTIFIED EMBED ---
    embed = discord.Embed(color=discord.Color.teal())
    embed.set_author(name=f"{interaction.user.display_name}'s Profile", icon_url=interaction.user.display_avatar.url)
    
    # 1. HERO SECTION: Global Tier
    embed.add_field(
        name="🏆 **Rank**", 
        value=(
            f"\u2003{g_tier_icon} **{g_tier_name}**\n"
            f"\u2003Global Rank: **#{g_rank_num}**"
        ), 
        inline=False
    )
    
    # 2. SERVER STATS
    embed.add_field(
        name=f"🏰 **{interaction.guild.name}**", 
        value=(
            f"\u2003🏅 Rank: **#{s_rank_num}**\n"
            f"\u2003🎗️ Tier: {s_tier_icon} **{s_tier_name}**\n"
            f"\u2003📊 Score: **{s_score:.2f}**\n"
            f"\u2003✅ Wins: **{s_wins}**\n"
            f"\u2003🎲 Games: **{s_games}**"
        ), 
        inline=True
    )
    
    # 3. GLOBAL STATS
    embed.add_field(
        name="🌍 **Global Aggregate**", 
        value=(
            f"\u2003🏅 Rank: **#{g_rank_num}**\n"
            f"\u2003🎗️ Tier: {g_tier_icon} **{g_tier_name}**\n"
            f"\u2003📊 Score: **{g_score:.2f}**\n"
            f"\u2003✅ Wins: **{g_wins}**\n"
            f"\u2003🎲 Games: **{g_games}**"
        ), 
        inline=True
    )
    
    # Footer for polish
    embed.set_footer(text="Keep playing to rank up!", icon_url=bot.user.display_avatar.url)
    
    await interaction.followup.send(embed=embed)
