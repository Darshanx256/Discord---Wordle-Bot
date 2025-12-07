import os
import random
import discord
from discord.ext import commands, tasks
from discord import ui 
import sqlite3
from dotenv import load_dotenv
import threading
from flask import Flask
import asyncio  
import sys
import datetime

# --- 1. CONFIGURATION ---
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

if not TOKEN:
    print("❌ FATAL: DISCORD_TOKEN not found.")
    exit(1)

SECRET_FILE = "words.txt"
VALID_FILE = "all_words.txt"
DB_NAME = 'wordle_leaderboard.db'
KEYBOARD_LAYOUT = ["QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM"]

# --- 2. RANKING & TIER CONFIGURATION ---
C_GAMES = 10  # Bayesian constant (Games)
C_WINRATE = 0.40 # Bayesian constant (Win Rate)

# emojis to represent tiers!!
TIERS = [
    (0.90, "💎", "Grandmaster"), 
    (0.65, "⚜️", "Master"),      
    (0.40, "⚔️", "Elite"),       
    (0.00, "🛡️", "Challenger")   
]

# ========= 3. UTILITY FUNCTIONS =========

def calculate_score(wins: int, games: int) -> float:
    """Calculates Bayesian average score for ranking."""
    if games == 0: return 0.0
    # Score = (Wins + Prior_Wins) / (Games + Prior_Games)
    return 10 * ((wins + (C_GAMES * C_WINRATE)) / (games + C_GAMES))

def get_tier_display(percentile: float) -> str:
    """Returns the Tier Icon and Name based on percentile rank."""
    for thresh, icon, name in TIERS:
        if percentile >= thresh: return icon, name
    return TIERS[-1][1], TIERS[-1][2] # Default to lowest

def get_markdown_keypad_status(used_letters: dict) -> str:

     #egg start
    extra_line = ""
    if random.randint(1,50) == 1:
        extra_line = "\n\n🦆 CONGRATULATIONS! You summoned a RARE Duck of Luck!\nHave a nice day!"
    #egg end

    
    """Generates the stylized keypad using Discord Markdown."""
    output_lines = []
    for row in KEYBOARD_LAYOUT:
        line = ""
        for char_key in row:
            char = char_key.lower()
            formatting = ""
            if char in used_letters['correct']: formatting = "**"
            elif char in used_letters['present']: formatting = "__"
            elif char in used_letters['absent']: formatting = "~~"
            line += f"{formatting}{char_key}{formatting} "
        output_lines.append(line.strip())

    output_lines[1] = u"\u2007" + output_lines[1]
    output_lines[2] = u"\u2007\u2007" + output_lines[2] 
    return "\n".join(output_lines) + extra_line + "\n\nLegend:\n**Bold** = Correct | __Underline__ = Misplaced | ~~Strikeout~~ = Absent"

def update_leaderboard(bot: commands.Bot, user_id: int, guild_id: int, won_game: bool):
    """Updates score."""
    if not guild_id: return 

    bot.db_cursor.execute("SELECT wins, total_games FROM scores WHERE user_id = ? AND guild_id = ?", (user_id, guild_id))
    row = bot.db_cursor.fetchone()
    
    cur_w, cur_g = row if row else (0, 0)
    new_w = cur_w + 1 if won_game else cur_w
    new_g = cur_g + 1

    bot.db_cursor.execute("""
        INSERT OR REPLACE INTO scores (user_id, guild_id, wins, total_games)
        VALUES (?, ?, ?, ?)
    """, (user_id, guild_id, new_w, new_g))
    bot.db_conn.commit()

def get_next_secret(bot: commands.Bot, guild_id: int) -> str:
    """Gets a secret word that hasn't been used in this guild, cycling when all are exhausted."""
    bot.db_cursor.execute("SELECT word FROM guild_history WHERE guild_id = ?", (guild_id,))
    used_words = {r[0] for r in bot.db_cursor.fetchall()}
    
    available_words = [w for w in bot.secrets if w not in used_words]
    
    if not available_words:
        # Reset the history if all words have been used
        bot.db_cursor.execute("DELETE FROM guild_history WHERE guild_id = ?", (guild_id,))
        bot.db_conn.commit()
        available_words = bot.secrets
        print(f"🔄 Guild {guild_id} history reset. Word pool recycled.")
        
    pick = random.choice(available_words)
    
    bot.db_cursor.execute("INSERT INTO guild_history (guild_id, word) VALUES (?, ?)", (guild_id, pick))
    bot.db_conn.commit()
    return pick

def get_win_flavor(attempts: int) -> str:
    """Returns a fun message based on how quickly they won."""
    if attempts == 1: return "🤯 IMPOSSIBLE! Pure luck or genius?"
    if attempts == 2: return "🔥 Insane! You read my mind."
    if attempts == 3: return "⚡ Blazing fast! Great job."
    if attempts == 4: return "👏 Solid performance."
    if attempts == 5: return "😅 Cutting it close..."
    return "💀 CLUTCH! That was stressful."

# --- PAGINATION VIEW CLASS ---
class LeaderboardView(discord.ui.View):
    def __init__(self, bot, data, title, color, interaction_user):
        super().__init__(timeout=60)
        self.bot = bot
        self.data = data  
        self.title = title
        self.color = color
        self.user = interaction_user
        self.current_page = 0
        self.items_per_page = 10
        self.total_pages = max(1, (len(data) - 1) // self.items_per_page + 1)
        self.update_buttons()

    def update_buttons(self):
        self.first_page.disabled = (self.current_page == 0)
        self.prev_page.disabled = (self.current_page == 0)
        self.next_page.disabled = (self.current_page == self.total_pages - 1)
        self.last_page.disabled = (self.current_page == self.total_pages - 1)

    def create_embed(self):
        start = self.current_page * self.items_per_page
        end = start + self.items_per_page
        page_data = self.data[start:end]

        description_lines = []
        if not page_data: description_lines.append("No data available.")
        else:
            scores = [d[5] for d in self.data]
            
            for rank, name, w, g, rate, score in page_data:
                # Determine Rank Icon and Tier
                rank_index = sum(1 for s in scores if s < score)
                perc = rank_index / len(scores) if scores else 0
                tier_icon, _ = get_tier_display(perc)

                medal = {1:"🥇", 2:"🥈", 3:"🥉"}.get(rank, f"`#{rank}`")
                
                description_lines.append(f"{medal} {tier_icon} **{name}**\n   > Score: **{score:.2f}** | Wins: {w} | Games: {g}")

        embed = discord.Embed(title=self.title, description="\n".join(description_lines), color=self.color)
        embed.set_footer(text=f"Page {self.current_page + 1}/{self.total_pages} • Total Players: {len(self.data)}")
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.user:
            await interaction.response.send_message("❌ This is not your menu.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="<<", style=discord.ButtonStyle.grey)
    async def first_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = 0
        self.update_buttons()
        await interaction.response.edit_message(embed=self.create_embed(), view=self)

    @discord.ui.button(label="<", style=discord.ButtonStyle.blurple)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.create_embed(), view=self)

    @discord.ui.button(label=">", style=discord.ButtonStyle.blurple)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.create_embed(), view=self)

    @discord.ui.button(label=">>", style=discord.ButtonStyle.grey)
    async def last_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = self.total_pages - 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.create_embed(), view=self)


# --- FLASK SERVER ---
def run_flask_server():
    app = Flask(__name__)
    @app.route('/')
    def home(): return "Bot OK", 200
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)


# ========= 4. GAME CLASS =========
class WordleGame:
    __slots__ = ('secret', 'channel_id', 'started_by', 'max_attempts', 'history', 
                 'used_letters', 'participants', 'guessed_words', 'last_interaction', 'message_id')

    def __init__(self, secret: str, channel_id: int, started_by: discord.abc.User, message_id: int):
        self.secret = secret
        self.channel_id = channel_id
        self.started_by = started_by
        self.max_attempts = 6
        self.history = [] 
        self.participants = set() 
        self.guessed_words = set()
        self.used_letters = {'correct': set(), 'present': set(), 'absent': set()}
        self.last_interaction = datetime.datetime.now()
        self.message_id = message_id

    @property
    def attempts_used(self): return len(self.history)

    def is_duplicate(self, word: str) -> bool: return word in self.guessed_words

    def evaluate_guess(self, guess: str) -> str:
        s_list = list(self.secret)
        g_list = list(guess)
        res = ["⬜"] * 5 
        cur_abs = set(guess) - set(s_list)

        # 1. Greens (Exact Matches)
        for i in range(5):
            if g_list[i] == s_list[i]:
                res[i] = "🟩"
                s_list[i] = None; g_list[i] = None
                self.used_letters['correct'].add(guess[i])
                self.used_letters['present'].discard(guess[i])

        # 2. Yellows (Misplaced)
        for i in range(5):
            if res[i] == "🟩": continue
            ch = g_list[i]
            if ch is not None and ch in s_list:
                res[i] = "🟨"
                s_list[s_list.index(ch)] = None
                if ch not in self.used_letters['correct']: self.used_letters['present'].add(ch)
            elif ch is not None: cur_abs.add(ch)

        # 3. Absents (Grey)
        self.used_letters['absent'].update(cur_abs - self.used_letters['correct'] - self.used_letters['present'])
        return "".join(res)

    def process_turn(self, guess: str, user):
        self.last_interaction = datetime.datetime.now()
        pat = self.evaluate_guess(guess)
        
        # Add to history and guessed_words ONLY after all validation/processing
        self.history.append({'word': guess, 'pattern': pat, 'user': user})
        self.participants.add(user.id)
        self.guessed_words.add(guess)
        
        return pat, (guess == self.secret), ((guess == self.secret) or (self.attempts_used >= self.max_attempts))


# ========= 5. BOT SETUP =========
class WordleBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents(guilds=True))
        self.games = {}      
        self.secrets = []; self.valid_set = set() 
        self.db_conn = None; self.db_cursor = None

    async def setup_hook(self):
        self.load_local_data()
        self.setup_db()
        await self.tree.sync()
        self.cleanup_task.start()
        print(f"✅ Ready! {len(self.secrets)} secrets.")
        
    async def close(self):
        if self.db_conn: self.db_conn.close()
        await super().close()

    def load_local_data(self):
        if os.path.exists(SECRET_FILE):
            with open(SECRET_FILE, "r", encoding="utf-8") as f:
                self.secrets = [w.strip().lower() for w in f if len(w.strip()) == 5]
        else: self.secrets = []

        if os.path.exists(VALID_FILE):
            with open(VALID_FILE, "r", encoding="utf-8") as f:
                self.valid_set = {w.strip().lower() for w in f if len(w.strip()) == 5}
        else: self.valid_set = set()
        self.valid_set.update(self.secrets)

    def setup_db(self):
        self.db_conn = sqlite3.connect(DB_NAME, check_same_thread=False)
        self.db_cursor = self.db_conn.cursor()
        self.db_cursor.execute("""
            CREATE TABLE IF NOT EXISTS scores (
                user_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,  
                wins INTEGER DEFAULT 0,
                total_games INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, guild_id) 
            )
        """)
        self.db_cursor.execute("""
            CREATE TABLE IF NOT EXISTS guild_history (
                guild_id INTEGER NOT NULL,
                word TEXT NOT NULL
            )
        """)
        self.db_conn.commit()

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
                except:
                    pass
        
        for cid in to_remove:
            self.games.pop(cid, None)

bot = WordleBot()


# ========= 6. EVENTS & COMMANDS =========

@bot.tree.command(name="wordle", description="Start a new game.")
async def start(interaction: discord.Interaction):
    if not interaction.guild: return await interaction.response.send_message("❌ Command must be used in a server.", ephemeral=True)
    
    if not bot.secrets:
        return await interaction.response.send_message("❌ Word list missing.", ephemeral=True)

    cid = interaction.channel_id
    if cid in bot.games:
        await interaction.response.send_message("⚠️ Game already active. Use `/stop_game` to end it.", ephemeral=True)
        return
        
    secret = get_next_secret(bot, interaction.guild_id)
    
    embed = discord.Embed(title="✨ Wordle Started!", color=discord.Color.blue())
    embed.description = "A **simple 5-letter word** has been chosen. **6 attempts** total."
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
    if not interaction.guild: return
    cid = interaction.channel_id
    g_word = word.lower().strip()
    game = bot.games.get(cid)

    if not game:
        return await interaction.response.send_message("⚠️ No active game. Start with `/wordle`.", ephemeral=True)

    if game.is_duplicate(g_word):
        return await interaction.response.send_message(f"⚠️ **{g_word.upper()}** was already guessed!", ephemeral=True)

    if len(g_word) != 5 or not g_word.isalpha():
        return await interaction.response.send_message("⚠️ 5 letters only.", ephemeral=True)
    if g_word not in bot.valid_set:
        return await interaction.response.send_message(f"⚠️ **{g_word.upper()}** not in dictionary.", ephemeral=True)

    pattern, win, game_over = game.process_turn(g_word, interaction.user)
    keypad = get_markdown_keypad_status(game.used_letters)
    
    # Progress Bar Logic (simple emojis)
    filled = "●" * game.attempts_used
    empty = "○" * (6 - game.attempts_used)
    progress_bar = f"[{filled}{empty}]"

    # Board Display
    board_display = "\n".join([f"`{h['word'].upper()}` {h['pattern']}" for h in game.history])
    
    # --- HINT SYSTEM ---
    hint_msg = ""
    # Trigger hint on 3rd attempt if no Green or Yellow tiles have been found across ALL history
    if game.attempts_used == 3:
        all_gray = all('🟩' not in x['pattern'] and '🟨' not in x['pattern'] for x in game.history)
        if all_gray:
            # Find a letter in the secret word that hasn't been successfully guessed yet
            known_absent = game.used_letters['absent']
            available_letters = [c for c in game.secret if c.lower() not in known_absent]

            if available_letters:
                hint_letter = random.choice(available_letters).upper()
                hint_msg = f"\n\n**💡 HINT!** The letter **{hint_letter}** is in the word."
    # ---

    if win:
        flavor = get_win_flavor(game.attempts_used)
        embed = discord.Embed(title=f"🏆 VICTORY!\n{flavor}", color=discord.Color.green())
        embed.description = f"**{interaction.user.mention}** found the word: **{game.secret.upper()}** in {game.attempts_used}/6 attempts!"
        embed.add_field(name="Final Board", value=board_display, inline=False)
        embed.add_field(name="Keyboard Status", value=keypad, inline=False)
    elif game_over:
        embed = discord.Embed(title="💀 GAME OVER", color=discord.Color.red())
        embed.description = f"The word was **{game.secret.upper()}**."
        embed.add_field(name="Final Board", value=board_display, inline=False)
        embed.add_field(name="Keyboard Status", value=keypad, inline=False)
    else:
        embed = discord.Embed(title=f"Attempt {game.attempts_used}/6", color=discord.Color.gold())
        embed.description = f"**{interaction.user.display_name}** guessed: `{g_word.upper()}`"
        embed.add_field(name="Current Board", value=board_display + hint_msg, inline=False)
        embed.add_field(name="Keyboard Status", value=keypad, inline=False)
        embed.set_footer(text=f"{6 - game.attempts_used} tries left {progress_bar}")
        
    await interaction.response.send_message(embed=embed)

    if game_over or win:
        winner_id = interaction.user.id if win else None
        for pid in game.participants:
            update_leaderboard(bot, pid, interaction.guild_id, (pid == winner_id))
        bot.games.pop(cid, None)

@bot.tree.command(name="wordle_board", description="View current board.")
async def board(interaction: discord.Interaction):
    game = bot.games.get(interaction.channel_id)
    if not game: return await interaction.response.send_message("❌ No active game.", ephemeral=True)
    
    board_display = "\n".join([f"`{h['word'].upper()}` {h['pattern']}" for h in game.history])

    embed = discord.Embed(title="📊 Current Board", description=board_display, color=discord.Color.blurple())
    embed.add_field(name="Keyboard Status", value=get_markdown_keypad_status(game.used_letters), inline=False)
    embed.set_footer(text=f"Attempts: {game.attempts_used}/6")
    await interaction.response.send_message(embed=embed)


# --- HELPER: Process Data for View (Optimized Network) ---
async def fetch_and_format_rankings(results, bot_instance, guild=None):
    formatted_data = []
    
    for i, (uid, w, g, s) in enumerate(results):
        
        name = f"User {uid}"
        if guild:
            member = guild.get_member(uid)
            if member:
                name = member.display_name
            else:
                try: u = await bot_instance.fetch_user(uid); name = u.display_name
                except: pass
        else:
             try: u = await bot_instance.fetch_user(uid); name = u.display_name
             except: pass

        formatted_data.append((i + 1, name, w, g, (w/g)*100 if g > 0 else 0, s))
        
    return formatted_data

@bot.tree.command(name="leaderboard", description="Server Leaderboard.")
async def leaderboard(interaction: discord.Interaction):
    if not interaction.guild: return
    
    bot.db_cursor.execute("""
        SELECT user_id, wins, total_games FROM scores 
        WHERE guild_id = ? 
    """, (interaction.guild_id,))
    results = bot.db_cursor.fetchall()

    if not results:
        return await interaction.response.send_message("No games played yet!", ephemeral=True)

    scored_results = sorted(
        [(uid, w, g, calculate_score(w, g)) for uid, w, g in results],
        key=lambda x: x[3], reverse=True
    )

    await interaction.response.defer() 
    data = await fetch_and_format_rankings(scored_results, bot, interaction.guild) 
    view = LeaderboardView(bot, data, f"🏆 {interaction.guild.name} Leaderboard", discord.Color.gold(), interaction.user)
    await interaction.followup.send(embed=view.create_embed(), view=view)

@bot.tree.command(name="leaderboard_global", description="Global Leaderboard.")
async def leaderboard_global(interaction: discord.Interaction):
    bot.db_cursor.execute("""
        SELECT user_id, SUM(wins) as t_wins, SUM(total_games) as t_games 
        FROM scores  
        GROUP BY user_id
    """)
    results = bot.db_cursor.fetchall()

    if not results:
        return await interaction.response.send_message("No global games yet!", ephemeral=True)

    scored_results = sorted(
        [(uid, w, g, calculate_score(w, g)) for uid, w, g in results],
        key=lambda x: x[3], reverse=True
    )

    await interaction.response.defer()
    data = await fetch_and_format_rankings(scored_results, bot) 
    view = LeaderboardView(bot, data, "🌍 Global Leaderboard", discord.Color.purple(), interaction.user)
    await interaction.followup.send(embed=view.create_embed(), view=view)

@bot.tree.command(name="profile", description="Check your personal stats.")
async def profile(interaction: discord.Interaction):
    if not interaction.guild: return
    uid = interaction.user.id
    
    # Get Server Stats
    bot.db_cursor.execute("SELECT wins, total_games FROM scores WHERE user_id = ? AND guild_id = ?", (uid, interaction.guild_id))
    s_row = bot.db_cursor.fetchone()
    s_wins, s_games = s_row if s_row else (0, 0)
    s_score = calculate_score(s_wins, s_games)

    # Determine Server Rank for Tier
    bot.db_cursor.execute("SELECT user_id, wins, total_games FROM scores WHERE guild_id = ?", (interaction.guild_id,))
    all_scores = [calculate_score(r[1], r[2]) for r in bot.db_cursor.fetchall()]
    all_scores.sort()
    rank_idx = sum(1 for s in all_scores if s < s_score)
    perc = rank_idx / len(all_scores) if all_scores else 0
    tier_icon, tier_name = get_tier_display(perc)


    # Get Global Stats
    bot.db_cursor.execute("SELECT SUM(wins), SUM(total_games) FROM scores WHERE user_id = ?", (uid,))
    g_row = bot.db_cursor.fetchone()
    g_wins, g_games = g_row if g_row and g_row[0] else (0, 0)
    g_score = calculate_score(g_wins, g_games)

    embed = discord.Embed(title=f"👤 Profile: {interaction.user.display_name}", color=discord.Color.teal())
    embed.add_field(name="Current Tier", value=f"{tier_icon} **{tier_name}**", inline=False)
    embed.add_field(name=f"🏰 {interaction.guild.name} Stats", value=f"Bayesian Score: **{s_score:.2f}**\nWins: {s_wins} | Games: {s_games}", inline=True)
    embed.add_field(name="🌍 Global Stats", value=f"Bayesian Score: **{g_score:.2f}**\nWins: {g_wins} | Games: {g_games}", inline=True)
    
    await interaction.response.send_message(embed=embed)


if __name__ == "__main__":
    t = threading.Thread(target=run_flask_server)
    t.start()
    bot.run(TOKEN)
