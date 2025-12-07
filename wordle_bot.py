import os
import random
import discord
from discord.ext import commands, tasks
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

# ========= 2. UTILITY FUNCTIONS =========

def get_markdown_keypad_status(used_letters: dict) -> str:
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
    return "\n".join(output_lines) + "\n\nLegend:\n**BOLD** = Correct | __UNDERLINE__ = Misplaced | ~~STRIKEOUT~~ = Absent"

def update_leaderboard(bot: commands.Bot, user_id: int, guild_id: int, won_game: bool):
    """Updates score. Uses existing (user_id, guild_id) schema."""
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
            for rank, name, w, g, rate in page_data:
                medal = {1:"🥇", 2:"🥈", 3:"🥉"}.get(rank, f"**{rank}.**")
                description_lines.append(f"{medal} **{name}**\n   > Wins: **{w}** | Games: {g} | Rate: {rate:.1f}%")

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


# ========= 3. GAME CLASS =========
class WordleGame:
    __slots__ = ('secret', 'channel_id', 'started_by', 'max_attempts', 'history', 
                 'used_letters', 'participants', 'guessed_words', 'last_interaction')

    def __init__(self, secret: str, channel_id: int, started_by: discord.abc.User):
        self.secret = secret
        self.channel_id = channel_id
        self.started_by = started_by
        self.max_attempts = 6
        self.history = [] 
        self.participants = set() 
        self.guessed_words = set()
        self.used_letters = {'correct': set(), 'present': set(), 'absent': set()}
        self.last_interaction = datetime.datetime.now() # Track time for timeout

    @property
    def attempts_used(self): return len(self.history)

    def is_duplicate(self, word: str) -> bool: return word in self.guessed_words

    def evaluate_guess(self, guess: str) -> str:
        s_list = list(self.secret)
        g_list = list(guess)
        res = ["⬜"] * 5
        cur_abs = set(guess) - set(s_list)

        for i in range(5):
            if g_list[i] == s_list[i]:
                res[i] = "🟩"
                s_list[i] = None; g_list[i] = None
                self.used_letters['correct'].add(guess[i])
                self.used_letters['present'].discard(guess[i])

        for i in range(5):
            if res[i] == "🟩": continue
            ch = g_list[i]
            if ch is not None and ch in s_list:
                res[i] = "🟨"
                s_list[s_list.index(ch)] = None
                if ch not in self.used_letters['correct']: self.used_letters['present'].add(ch)
            elif ch is not None: cur_abs.add(ch)

        self.used_letters['absent'].update(cur_abs - self.used_letters['correct'] - self.used_letters['present'])
        return "".join(res)

    def process_turn(self, guess: str, user):
        self.last_interaction = datetime.datetime.now() # Reset timeout timer
        pat = self.evaluate_guess(guess)
        self.history.append({'word': guess, 'pattern': pat, 'user': user})
        self.participants.add(user.id)
        self.guessed_words.add(guess)
        return pat, (guess == self.secret), ((guess == self.secret) or (self.attempts_used >= self.max_attempts))


# ========= 4. BOT SETUP =========
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
        self.cleanup_task.start() # Start the timeout loop
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
        self.db_conn.commit()
        print(f"🗄️ Database connected.")

    # --- 24H TIMEOUT TASK ---
    @tasks.loop(minutes=60) # Check every hour
    async def cleanup_task(self):
        now = datetime.datetime.now()
        to_remove = []
        for cid, game in self.games.items():
            delta = now - game.last_interaction
            if delta.total_seconds() > 86400: # 24 Hours
                to_remove.append(cid)
                try:
                    # Attempt to fetch channel and send timeout message
                    channel = self.get_channel(cid)
                    if channel:
                        embed = discord.Embed(title="⏰ Time's Up!", description=f"Game timed out due to inactivity.\nThe word was **{game.secret.upper()}**.", color=discord.Color.dark_grey())
                        await channel.send(embed=embed)
                except:
                    pass # Channel might be deleted or bot kicked
        
        for cid in to_remove:
            self.games.pop(cid, None)
            print(f"🧹 Cleaned up inactive game in channel {cid}")

bot = WordleBot()


# ========= 5. EVENTS & COMMANDS =========

@bot.event
async def on_error(event_method, *args, **kwargs):
    exc_type, exc_value, _ = sys.exc_info()
    if exc_type and issubclass(exc_type, discord.HTTPException):
        if hasattr(exc_value, 'status') and exc_value.status == 429:
            print(f"🛑 CRITICAL 429 RATE LIMIT. Restarting...")
            await asyncio.sleep(600)  
            await bot.close()
            sys.exit(0)
    print(f"⚠️ Error in {event_method}: {exc_value}")

@bot.tree.command(name="wordle", description="Start a new game.")
async def start(interaction: discord.Interaction):
    if not interaction.guild: return
    
    if not bot.secrets:
        return await interaction.response.send_message("❌ Word list missing.", ephemeral=True)

    cid = interaction.channel_id
    if cid in bot.games:
        # Ask to stop previous first? No, simplified override for now.
        bot.games.pop(cid, None) 
        
    secret = random.choice(bot.secrets)
    bot.games[cid] = WordleGame(secret, cid, interaction.user)

    embed = discord.Embed(title="✨ Wordle Started!", color=discord.Color.blue())
    embed.description = "A **simple 5-letter word** has been chosen. **6 attempts** total."
    embed.add_field(name="How to Play", value="`/guess word:xxxxx`", inline=False)
    embed.add_field(name="⚠️ Note", value="Stats are only recorded for players who make a guess!", inline=False)
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="stop_game", description="Force stop the current game (Manage Messages only).")
@commands.has_permissions(manage_messages=True)
async def stop_game(interaction: discord.Interaction):
    cid = interaction.channel_id
    if cid in bot.games:
        game = bot.games.pop(cid)
        await interaction.response.send_message(f"🛑 Game stopped by {interaction.user.mention}. The word was **{game.secret.upper()}**.")
    else:
        await interaction.response.send_message("No active game to stop.", ephemeral=True)

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
    
    hint_msg = ""
    if game.attempts_used == 3 and all('🟩' not in x['pattern'] and '🟨' not in x['pattern'] for x in game.history):
        cands = [c for c in game.secret if c not in {h['word'][i] for h in game.history for i in range(5) if h['pattern'][i] == '🟩'}]
        if cands: hint_msg = f"\n\n**💡 HINT!** The letter **{random.choice(cands).upper()}** is in the word."

    keypad = get_markdown_keypad_status(game.used_letters)
    
    # Progress Bar Logic
    filled = "●" * game.attempts_used
    empty = "○" * (6 - game.attempts_used)
    progress_bar = f"[{filled}{empty}]"

    if win:
        flavor = get_win_flavor(game.attempts_used)
        embed = discord.Embed(title=f"🏆 VICTORY! {flavor}", color=discord.Color.green())
        embed.description = f"**{interaction.user.mention}** found the word: **{game.secret.upper()}**!"
        embed.add_field(name="Final Result", value=f"{pattern} {g_word.upper()}")
        embed.add_field(name="Keyboard", value=keypad, inline=False)
    elif game_over:
        embed = discord.Embed(title="💀 GAME OVER", color=discord.Color.red())
        embed.description = f"The word was **{game.secret.upper()}**."
        embed.add_field(name="Last Guess", value=f"{pattern} {g_word.upper()}")
        embed.add_field(name="Keyboard", value=keypad, inline=False)
    else:
        embed = discord.Embed(title=f"Attempt {game.attempts_used}/6", color=discord.Color.gold())
        embed.description = f"**{interaction.user.display_name}**: `{g_word.upper()}`"
        embed.add_field(name="Result", value=f"{pattern} {g_word.upper()}{hint_msg}", inline=False)
        embed.add_field(name="Keyboard", value=keypad, inline=False)
        embed.set_footer(text=f"{6 - game.attempts_used} tries left {progress_bar}")
        
    await interaction.response.send_message(embed=embed)

    if game_over or win:
        winner_id = interaction.user.id if win else None
        for pid in game.participants:
            update_leaderboard(bot, pid, interaction.guild_id, (pid == winner_id))
        bot.games.pop(cid, None)

@bot.tree.command(name="wordle_board", description="View board.")
async def board(interaction: discord.Interaction):
    game = bot.games.get(interaction.channel_id)
    if not game: return await interaction.response.send_message("❌ No active game.", ephemeral=True)
    
    lines = [f"**{i}.** {x['pattern']} **{x['word'].upper()}**" for i, x in enumerate(game.history, 1)]
    embed = discord.Embed(title="📊 Board", description="\n".join(lines), color=discord.Color.blurple())
    embed.add_field(name="Keyboard", value=get_markdown_keypad_status(game.used_letters), inline=False)
    embed.set_footer(text=f"Attempts: {game.attempts_used}/6")
    await interaction.response.send_message(embed=embed)

# --- HELPER: Process Data for View ---
async def fetch_and_format_rankings(results, bot_instance):
    formatted_data = []
    for i, (uid, w, g) in enumerate(results, 1):
        try:
            u = await bot_instance.fetch_user(uid)
            name = u.display_name
        except: name = f"Unknown ({uid})"
        rate = (w/g)*100 if g>0 else 0
        formatted_data.append((i, name, w, g, rate))
    return formatted_data

@bot.tree.command(name="leaderboard", description="Server Leaderboard.")
async def leaderboard(interaction: discord.Interaction):
    if not interaction.guild: return
    
    bot.db_cursor.execute("""
        SELECT user_id, wins, total_games FROM scores 
        WHERE guild_id = ? ORDER BY wins DESC, total_games ASC
    """, (interaction.guild_id,))
    results = bot.db_cursor.fetchall()

    if not results:
        return await interaction.response.send_message("No games played yet!", ephemeral=True)

    await interaction.response.defer() 
    data = await fetch_and_format_rankings(results, bot)
    view = LeaderboardView(bot, data, f"🏆 {interaction.guild.name} Leaderboard", discord.Color.gold(), interaction.user)
    await interaction.followup.send(embed=view.create_embed(), view=view)

@bot.tree.command(name="leaderboard_global", description="Global Leaderboard.")
async def leaderboard_global(interaction: discord.Interaction):
    bot.db_cursor.execute("""
        SELECT user_id, SUM(wins) as t_wins, SUM(total_games) as t_games 
        FROM scores 
        GROUP BY user_id 
        ORDER BY t_wins DESC, t_games ASC
    """)
    results = bot.db_cursor.fetchall()

    if not results:
        return await interaction.response.send_message("No global games yet!", ephemeral=True)

    await interaction.response.defer()
    data = await fetch_and_format_rankings(results, bot)
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
    s_rate = (s_wins/s_games)*100 if s_games > 0 else 0

    # Get Global Stats
    bot.db_cursor.execute("SELECT SUM(wins), SUM(total_games) FROM scores WHERE user_id = ?", (uid,))
    g_row = bot.db_cursor.fetchone()
    g_wins, g_games = g_row if g_row and g_row[0] else (0, 0)
    g_rate = (g_wins/g_games)*100 if g_games > 0 else 0

    embed = discord.Embed(title=f"👤 Profile: {interaction.user.display_name}", color=discord.Color.teal())
    embed.add_field(name=f"🏰 {interaction.guild.name} Stats", value=f"Wins: **{s_wins}**\nGames: {s_games}\nWin Rate: {s_rate:.1f}%", inline=True)
    embed.add_field(name="🌍 Global Stats", value=f"Wins: **{g_wins}**\nGames: {g_games}\nWin Rate: {g_rate:.1f}%", inline=True)
    
    await interaction.response.send_message(embed=embed)

if __name__ == "__main__":
    t = threading.Thread(target=run_flask_server)
    t.start()
    bot.run(TOKEN)
