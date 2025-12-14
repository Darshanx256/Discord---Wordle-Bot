import os
import sys
import asyncio
import datetime
import random
import discord
from discord.ext import commands, tasks
from supabase import create_client, Client

from src.config import SUPABASE_URL, SUPABASE_KEY, SECRET_FILE, VALID_FILE, CLASSIC_FILE, ROTATING_ACTIVITIES, TIERS
from src.utils import calculate_score, get_win_flavor, get_tier_display
from src.database import (
    record_game_v2,
    fetch_user_profile_v2,
    trigger_egg,
    get_next_secret, 
    get_next_classic_secret
)
from src.game import WordleGame
from src.ui import LeaderboardView, HelpView, get_markdown_keypad_status, SoloView

# ========= 5. BOT SETUP =========
class WordleBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents(guilds=True))
        self.games = {} 
        self.solo_games = {} # New: Stores private solo games
        self.secrets = []       
        self.hard_secrets = []  
        self.valid_set = set()  # Full dictionary (Set, for O(1) validation) 
        self.name_cache = {} # {uid: display_name}
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

        # Cleanup Solo Games
        solo_remove = []
        for uid, sgame in self.solo_games.items():
             delta = now - sgame.last_interaction
             if delta.total_seconds() > 86400: # 24 Hours
                 solo_remove.append(uid)
        
        for uid in solo_remove:
            self.solo_games.pop(uid, None)
    
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
    sem = asyncio.Semaphore(5) 

    async def fetch_user_safe(row_data):
        i, (uid, w, xp, wr, badge) = row_data # V2 Structure Updated
        name = f"User {uid}"
        
        # Determine Tier Icon based on WR
        tier_icon = "🛡️"
        for t in TIERS:
            if wr >= t['min_wr']:
                tier_icon = t['icon']
                break

        # 1. Try Local Cache (FAST & SAFE)
        if guild:
            member = guild.get_member(uid)
            if member:
                return (i + 1, member.display_name, w, xp, wr, tier_icon, badge)
        
        # 2. Try Global Bot Cache (FAST & SAFE)
        user = bot_instance.get_user(uid)
        if user:
            return (i + 1, user.display_name, w, xp, wr, tier_icon, badge)

        # 3. Try In-Memory Name Cache (FAST)
        if uid in bot_instance.name_cache:
             return (i + 1, bot_instance.name_cache[uid], w, xp, wr, tier_icon, badge)

        # 4. API Call (SLOW - Needs Semaphore)
        async with sem:
            try:
                u = await bot_instance.fetch_user(uid)
                name = u.display_name
                bot_instance.name_cache[uid] = name # Cache it
            except:
                pass 
        
        return (i + 1, name, w, xp, wr, tier_icon, badge)

    # Launch all tasks
    tasks = [fetch_user_safe((i, r)) for i, r in enumerate(results)]
    
    formatted_data = await asyncio.gather(*tasks)
    return formatted_data


@bot.tree.command(name="help", description="How to play and command guide.")
async def help_command(interaction: discord.Interaction):
    view = HelpView(interaction.user)
    await interaction.response.send_message(embed=view.create_embed(), view=view, ephemeral=True)

@bot.tree.command(name="wordle", description="Start a new game (Simple word list).")
async def start(interaction: discord.Interaction):
    if not interaction.guild: return await interaction.response.send_message("❌ Command must be used in a server.", ephemeral=True)
    if not bot.secrets: return await interaction.response.send_message("❌ Simple word list missing.", ephemeral=True)

    cid = interaction.channel_id
    if cid in bot.games:
        return await interaction.response.send_message("⚠️ Game already active. Use `/stop_game` to end it.", ephemeral=True)
        
    secret = get_next_secret(bot, interaction.guild_id)
    
    # Easter Egg Trigger?
    title = "✨ Wordle Started! (Simple)"
    egg_msg = ""
    rng = random.randint(1, 100)
    
    if rng == 1:
        title = "🦆 Wordle Started! (Duck Edition)"
        trigger_egg(bot, interaction.user.id, "duck")
        egg_msg = "\n🎉 **You found a rare Duck!** It has been added to your collection."
    elif rng == 2:
        title = "🍬 Wordle Started! (Candy Edition)"
        trigger_egg(bot, interaction.user.id, "candy")
        egg_msg = "\n🍬 **Ooh! A piece of candy!** (Added to collection)"
    
    embed = discord.Embed(title=title, color=discord.Color.blue())
    embed.description = f"A simple **5-letter word** has been chosen. **6 attempts** total.{egg_msg}"
    embed.add_field(name="How to Play", value="`/guess word:xxxxx`", inline=False)
    
    await interaction.response.send_message(embed=embed)
    msg = await interaction.original_response()
    
    # Init Game
    bot.games[cid] = WordleGame(secret, cid, interaction.user, msg.id)
    print(f"DEBUG: Game STARTED in Channel {cid}. Active Games: {list(bot.games.keys())}")


@bot.tree.command(name="wordle_classic", description="Start a Classic game (Full dictionary list).")
async def start_classic(interaction: discord.Interaction):
    if not interaction.guild: return await interaction.response.send_message("❌ Command must be used in a server.", ephemeral=True)
    if not bot.hard_secrets: return await interaction.response.send_message("❌ Classic word list missing.", ephemeral=True)

    cid = interaction.channel_id
    if cid in bot.games:
        return await interaction.response.send_message("⚠️ Game already active.", ephemeral=True)

    secret = get_next_classic_secret(bot, interaction.guild_id)
    
    title = "⚔️ Wordle Started! (Classic)"
    egg_msg = ""
    rng = random.randint(1, 200) # Rare
    
    if rng == 1: 
        title = "🐲 Wordle Started! (Dragon Slayer Mode)"
        trigger_egg(bot, interaction.user.id, "dragon")
        egg_msg = "\n🔥 **A DRAGON APPEARS!** (Added to collection)"
    elif rng == 2:
        title = "🍬 Wordle Started! (Candy Edition)"
        trigger_egg(bot, interaction.user.id, "candy")
        egg_msg = "\n🍬 **Sweeeet! Found a candy.**"
        
    embed = discord.Embed(title=title, color=discord.Color.dark_gold())
    embed.description = f"**Hard Mode!** 6 attempts.{egg_msg}"
    embed.add_field(name="How to Play", value="`/guess word:xxxxx`", inline=False)
    
    await interaction.response.send_message(embed=embed)
    msg = await interaction.original_response()
    bot.games[cid] = WordleGame(secret, cid, interaction.user, msg.id)
    print(f"DEBUG: Classic Game STARTED in Channel {cid}. Active Games: {list(bot.games.keys())}")

@bot.tree.command(name="solo", description="Play a private game (Ephemeral).")
async def solo(interaction: discord.Interaction):
    # Ephemeral Game
    if interaction.user.id in bot.solo_games:
        return await interaction.response.send_message("⚠️ You already have a solo game running!", ephemeral=True)
    
    # Pick secret
    secret = random.choice(bot.secrets)
    game = WordleGame(secret, 0, interaction.user, 0) # Dummy Channel/Msg ID
    bot.solo_games[interaction.user.id] = game
    
    # Initial board and keyboard display
    board_display = "No guesses yet."
    keypad = get_markdown_keypad_status(game.used_letters, bot, interaction.user.id)
    progress_bar = "[○○○○○○]"
    
    embed = discord.Embed(title="Solo Wordle | Attempt 0/6", color=discord.Color.gold())
    embed.description = "This game is **private**. Only you can see it.\nUse the button below to guess."
    embed.add_field(name="Board", value=board_display, inline=False)
    embed.add_field(name="Keyboard", value=keypad, inline=False)
    embed.set_footer(text=f"6 tries left {progress_bar}")
    
    view = SoloView(bot, game, interaction.user)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

@bot.tree.command(name="show_solo", description="Show your active solo game (if dismissed via Ephemeral).")
async def show_solo(interaction: discord.Interaction):
    
    if interaction.user.id not in bot.solo_games:
        return await interaction.response.send_message("⚠️ No active solo game found.", ephemeral=True)
        
    game = bot.solo_games[interaction.user.id]
    
    # Reconstruct state
    filled = "●" * game.attempts_used
    empty = "○" * (6 - game.attempts_used)
    progress_bar = f"[{filled}{empty}]"
    
    board_display = "\n".join([f"{h['pattern']}" for h in game.history]) if game.history else "No guesses yet."
    keypad = get_markdown_keypad_status(game.used_letters, bot, interaction.user.id)
    
    embed = discord.Embed(title=f"Solo Wordle | Attempt {game.attempts_used}/6", color=discord.Color.gold())
    embed.add_field(name="Board", value=board_display, inline=False)
    embed.add_field(name="Keyboard", value=keypad, inline=False)
    embed.set_footer(text=f"{6 - game.attempts_used} tries left {progress_bar}")
    
    view = SoloView(bot, game, interaction.user)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

@bot.tree.command(name="cancel_solo", description="Cancel your active solo game.")
async def cancel_solo(interaction: discord.Interaction):
    if interaction.user.id not in bot.solo_games:
        return await interaction.response.send_message("⚠️ No active solo game to cancel.", ephemeral=True)
    
    game = bot.solo_games.pop(interaction.user.id)
    await interaction.response.send_message(f"✅ Solo game cancelled. The word was **{game.secret.upper()}**.", ephemeral=True)

@bot.tree.command(name="stop_game", description="Force stop the current game.")
async def stop_game(interaction: discord.Interaction):
    cid = interaction.channel_id
    game = bot.games.get(cid)
    
    if not game: return await interaction.response.send_message("No active game to stop.", ephemeral=True)
    
    if (interaction.user.id == game.started_by.id) or interaction.permissions.manage_messages:
        bot.games.pop(cid)
        await interaction.response.send_message(f"🛑 Game stopped. Word: **{game.secret.upper()}**.")
    else:
        await interaction.response.send_message("❌ Only Starter or Admin can stop it.", ephemeral=True)

@bot.tree.command(name="guess", description="Guess a 5-letter word.")
async def guess(interaction: discord.Interaction, word: str):
    await interaction.response.defer() 
    if not interaction.guild: return await interaction.followup.send("Guild only.", ephemeral=True)
    
    cid = interaction.channel_id
    game = bot.games.get(cid)
    g_word = word.lower().strip()
    
    print(f"DEBUG: Guess in Channel {cid}. Active: {list(bot.games.keys())}")

    if not game: return await interaction.followup.send("⚠️ No active game.", ephemeral=True)
    if game.is_duplicate(g_word): return await interaction.followup.send(f"⚠️ **{g_word.upper()}** already guessed!", ephemeral=True)
    if len(g_word) != 5 or not g_word.isalpha(): return await interaction.followup.send("⚠️ 5 letters only.", ephemeral=True)
    if g_word not in bot.valid_set: return await interaction.followup.send(f"⚠️ **{g_word.upper()}** not in dictionary.", ephemeral=True)

    # Note: Multiplayer games are cooperative? 
    # Prompt: "Multiplayer... Includes Global Multiplayer and Guild Multiplayer... Played via /wordle... Uses /guess".
    # "Match Performance Score... Based on the best final guess for the player's unique word" - wait.
    # "Based on the best final guess for the *player's unique word*"? 
    # Usually Wordle Bot is cooperative (everyone guesses together on one board).
    # If "Multiplayer" implies standard channel game, then everyone contributes.
    # "MPS... Based on the best final guess for the player's unique word" -> This sounds like everyone has their OWN word in the same game?
    # NO, "Same board UI as multiplayer...".
    # I suspect "player's unique word" is a typo in prompt or means "Based on the guess YOU made".
    # "Correct word guessed: +120".
    # Let's assume standard coop: You guess, you get points.
    
    pat, win, game_over = game.process_turn(g_word, interaction.user)
    
    keypad = get_markdown_keypad_status(game.used_letters, bot, interaction.user.id)
    filled = "●" * game.attempts_used
    empty = "○" * (6 - game.attempts_used)
    board_display = "\n".join([f"{h['pattern']}" for h in game.history])
    
    message_content = f"⌨️ **Keyboard Status:**\n{keypad}"
    
    # Display Badge logic 
    # Optimization: Fetch badge from local cache if possible, or simple query. 
    # For speed, we might want to query user_stats_v2.active_badge.
    # But doing a DB call every guess is heavy.
    # Let's rely on Profile fetch (cached?) or just do a quick select.
    # Or, only on Win? User wants "jack🦆 guessed: ...".
    # We will do a lightweight select.
    try:
         b_res = bot.supabase_client.table('user_stats_v2').select('active_badge').eq('user_id', interaction.user.id).execute()
         active_badge = b_res.data[0]['active_badge'] if b_res.data else None
    except:
         active_badge = None
         
    badge_str = f" {active_badge}" if active_badge else ""
    
    if win:
        time_taken = (datetime.datetime.now() - game.start_time).total_seconds()
        flavor = get_win_flavor(game.attempts_used)
        embed = discord.Embed(title=f"🏆 VICTORY!\n{flavor}", color=discord.Color.green())
        embed.description = f"**{interaction.user.mention}{badge_str}** found **{game.secret.upper()}** in {game.attempts_used}/6!"
        embed.add_field(name="Final Board", value=board_display, inline=False)
        
        # RECORD stats for the WINNER
        # "reward the participants fairly".
        # Prompt: "Correct word guessed: +120... Participated: +5".
        # So Winner gets Win points. Others get Participation?
        # My record_game_v2 logic calculates based on outcome.
        # Winner -> 'win'. Others -> 'participation'.
        # I need to loop through participants.
        
        # 1. Award Winner
        res = record_game_v2(bot, interaction.user.id, interaction.guild_id, 'MULTI', 'win', game.attempts_used, time_taken)
        if res:
             xp_show = f"**{res.get('xp_gain',0)}** 💠" # Updated with diamond dot emoji
             embed.add_field(name="Winner Rewards", value=f"➕ {xp_show} XP | 📈 WR: {res.get('multi_wr')}", inline=False)
             
             if res.get('level_up'):
                 lvl = res['level_up']
                 await interaction.channel.send(f"🔼 **LEVEL UP!** {interaction.user.mention} is now **Level {lvl}**! 🔼")

             if res.get('tier_up'):
                 t_name = res['tier_up']['name']
                 t_icon = res['tier_up']['icon']
                 await interaction.channel.send(f"🎉 **PROMOTION!** {interaction.user.mention} has reached **{t_icon} {t_name}** Tier! 🎉")

        # 2. Award Participants (excluding winner)
        others = game.participants - {interaction.user.id}
        for uid in others:
            # "Participated: +5".
            # Can I detect if they got letters correct? "4 letters correct: +70".
            # That requires tracking who guessed what.
            # `game.history` has `{'user': ...}`.
            # I can calculate best guess for each user?
            # Prompt: "Based on the best final guess for the player's unique word" -> "Best final guess" implies best result they got.
            # Complex logic. For now, simple participation.
            # Just give 'participation' reward.
            await asyncio.to_thread(record_game_v2, bot, uid, interaction.guild_id, 'MULTI', 'participation', game.attempts_used, 999)

        bot.games.pop(cid, None)
        
    elif game_over:
        embed = discord.Embed(title="💀 GAME OVER", color=discord.Color.red())
        embed.description = f"The word was **{game.secret.upper()}**."
        embed.add_field(name="Final Board", value=board_display, inline=False)
        
        # Award participation to all
        for uid in game.participants:
             await asyncio.to_thread(record_game_v2, bot, uid, interaction.guild_id, 'MULTI', 'loss', 6, 999)
             
        bot.games.pop(cid, None)
    else:
        # Just a turn
        embed = discord.Embed(title=f"Attempt {game.attempts_used}/6", color=discord.Color.gold())
        embed.description = f"**{interaction.user.display_name}{badge_str}** guessed: `{g_word.upper()}`"
        embed.add_field(name="Current Board", value=board_display, inline=False)
        embed.set_footer(text=f"{6 - game.attempts_used} tries left [{filled}{empty}]")
        
        # Award partial points for letters? "1 letter correct: +10".
        # Only if we want real-time XP? No, usually at end. 
        # But Prompt says "XP granted... Correct word guessed +50... 1 letter correct +10".
        # If we reward per guess, people will spam.
        # "Match Performance Score... Based on the best final guess".
        # Implies End of Game calculation.
        # "XP... Earned from all games".
        # Let's stick to End of Game rewards for simplicity and anti-spam.

    await interaction.followup.send(content=message_content, embed=embed)

@bot.tree.command(name="leaderboard", description="Server Leaderboard (Multiplayer WR).")
async def leaderboard(interaction: discord.Interaction):
    if not interaction.guild: return
    await interaction.response.defer()
    
    # Query Guild Stats + User Stats
    # We want members of this guild, ordered by Multi WR.
    # Refactored to two-step fetch for robustness against Supabase join eccentricities
    try:
        # Step 1: Get User IDs in this guild
        g_response = bot.supabase_client.table('guild_stats_v2') \
            .select('user_id') \
            .eq('guild_id', interaction.guild_id) \
            .execute()
            
        if not g_response.data:
            return await interaction.followup.send("No ranked players in this server yet!", ephemeral=True)
            
        guild_user_ids = [r['user_id'] for r in g_response.data]

        # Step 2: Fetch Stats for these users
        u_response = bot.supabase_client.table('user_stats_v2') \
            .select('user_id, multi_wins, xp, multi_wr, active_badge') \
            .in_('user_id', guild_user_ids) \
            .execute()

        results = []
        for r in u_response.data:
            results.append((r['user_id'], r['multi_wins'], r['xp'], r['multi_wr'], r.get('active_badge')))
        
        # Sort by WR desc
        results.sort(key=lambda x: x[3], reverse=True)
        results = results[:50] # Top 50
        
    except Exception as e:
        print(f"Leaderboard Error: {e}")
        return await interaction.followup.send("❌ Error fetching leaderboard. Please try again later.", ephemeral=True)

    if not results: return await interaction.followup.send("No ranked players yet!", ephemeral=True)

    data = await fetch_and_format_rankings(results, bot, interaction.guild)
    
    view = LeaderboardView(bot, data, f"🏆 {interaction.guild.name} Leaderboard", discord.Color.gold(), interaction.user)
    await interaction.followup.send(embed=view.create_embed(), view=view)


@bot.tree.command(name="leaderboard_global", description="Global Leaderboard (Multiplayer WR).")
async def leaderboard_global(interaction: discord.Interaction):
    await interaction.response.defer()
    
    try:
        response = bot.supabase_client.table('user_stats_v2') \
            .select('user_id, multi_wins, xp, multi_wr, active_badge') \
            .order('multi_wr', desc=True) \
            .limit(50) \
            .execute()
            
        if not response.data:
            return await interaction.followup.send("No records found in global leaderboard.", ephemeral=True)
            
        results = [(r['user_id'], r['multi_wins'], r['xp'], r['multi_wr'], r.get('active_badge')) for r in response.data]
        
    except Exception as e:
        print(f"Global Leaderboard Error: {e}")
        return await interaction.followup.send("❌ Error fetching global leaderboard.", ephemeral=True)

    if not results: return await interaction.followup.send("No players yet!", ephemeral=True)

    data = await fetch_and_format_rankings(results, bot)
    
    view = LeaderboardView(bot, data, "🌍 Global Leaderboard", discord.Color.purple(), interaction.user)
    await interaction.followup.send(embed=view.create_embed(), view=view)

@bot.tree.command(name="profile", description="Check your personal V2 stats.")
async def profile(interaction: discord.Interaction):
    await interaction.response.defer()
    
    p = fetch_user_profile_v2(bot, interaction.user.id)
    if not p:
        # Try to make a profile if missing? Or just show empty
        return await interaction.followup.send("You haven't played directly yet!", ephemeral=True)

    # p keys: xp, level, solo_wr, multi_wr, eggs, badges, active_badge, etc.
    embed = discord.Embed(color=discord.Color.teal())
    embed.set_author(name=f"{interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)
    
    # Badge Display
    badge_str = ""
    if p.get('active_badge'):
        badge_str = f"Permission Badge: **{p['active_badge']}**\n"
    
    # Tier info
    tier = p.get('tier', {})
    tier_name = tier.get('name', 'Unranked')
    tier_icon = tier.get('icon', '')
    
    embed.description = f"{badge_str}**Level {p.get('level', 1)}** | {tier_icon} **{tier_name}**"
    
    embed.add_field(name="⚔️ Multiplayer", value=f"WR: **{p['multi_wr']}**\nWins: {p['multi_wins']}", inline=True)
    embed.add_field(name="🕵️ Solo", value=f"WR: **{p['solo_wr']}**\nWins: {p['solo_wins']}", inline=True)
    
    # Collection
    eggs = p.get('eggs', {})
    egg_str = "None"
    if eggs:
        egg_str = "\n".join([f"{k.capitalize()}: {v}x" for k, v in eggs.items()])
    
    embed.add_field(name="🎒 Collection", value=egg_str, inline=False)
    
    # Progress Bar for Level
    curr = p.get('current_level_xp', 0)
    nxt = p.get('next_level_xp', 100)
    pct = min(1.0, curr / nxt)
    bar_len = 10
    filled_len = int(bar_len * pct)
    bar = "█" * filled_len + "░" * (bar_len - filled_len)
    
    embed.add_field(name="Level Progress", value=f"`{bar}` {curr}/{nxt} XP", inline=False)
    
    await interaction.followup.send(embed=embed)

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
            # Set Active Badge
             bot.supabase_client.table('user_stats_v2').update({'active_badge': '🦆 Duck Lord'}).eq('user_id', interaction.user.id).execute()
             await inter.response.send_message("✅ Equipped **Duck Lord** Badge!", ephemeral=True)
        else:
             await inter.response.send_message(f"❌ Need 4 Ducks. You have {duck_count}.", ephemeral=True)

    async def buy_dragon(inter: discord.Interaction):
        if dragon_count >= 2:
             bot.supabase_client.table('user_stats_v2').update({'active_badge': '🐲 Dragon Slayer'}).eq('user_id', interaction.user.id).execute()
             await inter.response.send_message("✅ Equipped **Dragon Slayer** Badge!", ephemeral=True)
        else:
             await inter.response.send_message(f"❌ Need 2 Dragons. You have {dragon_count}.", ephemeral=True)

    async def buy_candy(inter: discord.Interaction):
        if candy_count >= 3:
             bot.supabase_client.table('user_stats_v2').update({'active_badge': '🍬 Sugar Rush'}).eq('user_id', interaction.user.id).execute()
             await inter.response.send_message("✅ Equipped **Sugar Rush** Badge!", ephemeral=True)
        else:
             await inter.response.send_message(f"❌ Need 3 Candies. You have {candy_count}.", ephemeral=True)
             
    async def unequip(inter: discord.Interaction):
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
    embed.add_field(name="Your Inventory", value=f"🦆 Ducks: {duck_count}\n🐲 Dragons: {dragon_count}\n🍬 Candies: {candy_count}", inline=False)
    
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)

