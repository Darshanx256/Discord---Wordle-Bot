import os
import random
import discord
from discord.ext import commands
import sqlite3
from dotenv import load_dotenv
import threading
from flask import Flask
import asyncio  # Required for smart rate limit delay
import sys      # Required for clean process exit

# --- 1. CONFIGURATION AND ENVIRONMENT SETUP ---
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

if not TOKEN:
    print("❌ FATAL: DISCORD_TOKEN not found in .env file.")
    exit(1)

SECRET_FILE = "words.txt"
VALID_FILE = "all_words.txt"
DB_NAME = 'wordle_leaderboard.db'
KEYBOARD_LAYOUT = [
    "QWERTYUIOP",
    "ASDFGHJKL",
    "ZXCVBNM"
]

# ========= 2. UTILITY FUNCTIONS (Frontend & Database) =========

def get_markdown_keypad_status(used_letters: dict) -> str:
    """Generates the stylized keypad using Discord Markdown."""
    
    output_lines = []
    
    for row in KEYBOARD_LAYOUT:
        line = ""
        for char_key in row:
            char = char_key.lower()
            formatting = ""
            
            if char in used_letters['correct']:
                formatting = "**"
            elif char in used_letters['present']:
                formatting = "__"
            elif char in used_letters['absent']:
                formatting = "~~"
            
            line += f"{formatting}{char_key}{formatting} "
            
        output_lines.append(line.strip())

    output_lines[1] = u"\u2007" + output_lines[1]
    output_lines[2] = u"\u2007\u2007" + output_lines[2] 
    
    legend = "\n\nLegend:\n**BOLD** = Correct | __UNDERLINE__ = Misplaced | ~~STRIKEOUT~~ = Absent"

    return "\n".join(output_lines) + legend


def update_leaderboard(bot: commands.Bot, user_id: int, guild_id: int, win: bool):
    """Updates the user's score in the SQLite database for a specific guild."""
    
    bot.db_cursor.execute("SELECT wins, total_games FROM scores WHERE user_id = ? AND guild_id = ?", 
                          (user_id, guild_id))
    row = bot.db_cursor.fetchone()
    
    current_wins = 0
    current_games = 0

    if row:
        current_wins, current_games = row
    
    new_wins = current_wins + 1 if win else current_wins
    new_games = current_games + 1

    bot.db_cursor.execute("""
        INSERT OR REPLACE INTO scores (user_id, guild_id, wins, total_games)
        VALUES (?, ?, ?, ?)
    """, (user_id, guild_id, new_wins, new_games))
    
    bot.db_conn.commit()


# --- FLASK SERVER FOR RENDER HEALTH CHECK ---
def run_flask_server():
    """Starts a simple Flask server in a thread to satisfy Render's port requirement."""
    app = Flask(__name__)
    
    @app.route('/')
    def home():
        return "Discord Bot is Running!", 200

    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)

# ---------------------------------------------


# ========= 3. GAME CLASS (High Performance) =========
class WordleGame:
    __slots__ = ('secret', 'channel_id', 'started_by', 'max_attempts', 'history', 'used_letters')

    def __init__(self, secret: str, channel_id: int, started_by: discord.abc.User):
        self.secret = secret
        self.channel_id = channel_id
        self.started_by = started_by
        self.max_attempts = 6
        self.history = [] 
        self.used_letters = {
            'correct': set(),  
            'present': set(),  
            'absent': set()    
        }

    @property
    def attempts_used(self):
        return len(self.history)

    def evaluate_guess(self, guess: str) -> str:
        secret_list = list(self.secret)
        guess_list = list(guess)
        result = ["⬜"] * 5
        
        current_absent = set(guess) - set(secret_list)

        for i in range(5):
            char = guess_list[i]
            if char == secret_list[i]:
                result[i] = "🟩"
                secret_list[i] = None 
                guess_list[i] = None
                self.used_letters['correct'].add(char)
                self.used_letters['present'].discard(char) 

        for i in range(5):
            if result[i] == "🟩": continue
            char = guess_list[i]
            if char is not None and char in secret_list:
                result[i] = "🟨"
                secret_list[secret_list.index(char)] = None 
                
                if char not in self.used_letters['correct']:
                    self.used_letters['present'].add(char)
            elif char is not None:
                current_absent.add(char)

        self.used_letters['absent'].update(current_absent - self.used_letters['correct'] - self.used_letters['present'])
        
        return "".join(result)

    def process_turn(self, guess: str, user):
        pattern = self.evaluate_guess(guess)
        self.history.append({'word': guess, 'pattern': pattern, 'user': user})
        
        is_win = (guess == self.secret)
        is_over = is_win or (self.attempts_used >= self.max_attempts)
        return pattern, is_win, is_over


# ========= 4. BOT SETUP AND LIFECYCLE =========
class WordleBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents(guilds=True))
        self.games = {}      
        self.secrets = []    
        self.valid_set = set() 
        
        self.db_conn = None
        self.db_cursor = None

    async def setup_hook(self):
        self.load_local_data()
        self.setup_db()
        await self.tree.sync()
        print(f"✅ Ready! Loaded {len(self.secrets)} secrets and {len(self.valid_set)} dictionary words.")
        
    async def close(self):
        if self.db_conn:
            self.db_conn.close()
            print(f"🗄️ Database '{DB_NAME}' connection closed.")
        await super().close()

    def load_local_data(self):
        if os.path.exists(SECRET_FILE):
            with open(SECRET_FILE, "r", encoding="utf-8") as f:
                self.secrets = [w.strip().lower() for w in f if len(w.strip()) == 5]
        else:
            print(f"⚠️ WARNING: {SECRET_FILE} not found. Game cannot start.")

        if os.path.exists(VALID_FILE):
            with open(VALID_FILE, "r", encoding="utf-8") as f:
                self.valid_set = {w.strip().lower() for w in f if len(w.strip()) == 5}
        else:
            print(f"⚠️ WARNING: {VALID_FILE} not found. Validation disabled.")

        self.valid_set.update(self.secrets)

    def setup_db(self):
        self.db_conn = sqlite3.connect(DB_NAME)
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
        print(f"🗄️ Database '{DB_NAME}' connected and schema verified.")

bot = WordleBot()


# ========= 5. COMMANDS & EVENT HANDLERS (With Rate Limit Logic) =========

@bot.event
async def on_error(event_method, *args, **kwargs):
    """
    Catches unhandled errors, specifically critical HTTPExceptions (like 429 rate limits),
    and initiates a delayed exit/restart.
    """
    
    exc_type, exc_value, _ = sys.exc_info()
    
    # Check for the primary HTTP Exception type that indicates a persistent block
    if exc_type is not None and issubclass(exc_type, discord.HTTPException):
        
        if hasattr(exc_value, 'status') and exc_value.status == 429:
            print(f"\n🛑🛑 CRITICAL RATE LIMIT (429) DETECTED 🛑🛑")
            print("Action: Initiating safe shutdown and forced restart after 10 minutes to clear IP ban.")
            
            # 1. Wait a long time (10 minutes) to clear the temporary ban.
            await asyncio.sleep(600)  
            
            # 2. Exit the Python process. Render will detect the exit and restart the worker.
            await bot.close()
            sys.exit(0)
        
        else:
            print(f"⚠️ Unhandled API Error in {event_method}: {exc_value}")
            
    else:
        # Default behavior for non-API errors
        print(f"❌ Unhandled error in {event_method}: {exc_type}: {exc_value}") 


@bot.tree.command(name="wordle", description="Start a game with a Simple Secret Word.")
async def start(interaction: discord.Interaction):
    cid = interaction.channel_id
    
    if not bot.secrets:
        await interaction.response.send_message("❌ System Error: Word list missing.", ephemeral=True)
        return

    bot.games.pop(cid, None)
    secret = random.choice(bot.secrets)
    bot.games[cid] = WordleGame(secret, cid, interaction.user)

    embed = discord.Embed(
        title="✨ Multiplayer Wordle Challenge Started! ✨", 
        color=discord.Color.blue()
    )
    embed.description = (
        f"A **simple 5-letter word** has been chosen. You have **6 attempts** as a team.\n"
        f"Everyone in the channel can participate! Good luck!"
    )
    embed.add_field(
        name="How to Guess", 
        value="Type `/guess word:xxxxx`", 
        inline=False
    )
    
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="guess", description="Guess any valid 5-letter English word.")
async def guess(interaction: discord.Interaction, word: str):
    cid = interaction.channel_id
    guess_word = word.lower().strip()
    game = bot.games.get(cid)

    if not game:
        return await interaction.response.send_message("⚠️ No active game. Start with `/wordle`.", ephemeral=True)

    if len(guess_word) != 5 or not guess_word.isalpha():
        return await interaction.response.send_message("⚠️ Word must be 5 letters (A-Z only).", ephemeral=True)
    if guess_word not in bot.valid_set:
        return await interaction.response.send_message(f"⚠️ **{guess_word.upper()}** is not in the English dictionary.", ephemeral=True)

    pattern, win, game_over = game.process_turn(guess_word, interaction.user)
    
    hint_message = ""
    keypad_status = get_markdown_keypad_status(game.used_letters)
    
    # ... (Embed generation remains the same) ...

    if win:
        embed = discord.Embed(title="🏆 VICTORY!", color=discord.Color.green())
        embed.description = f"**{interaction.user.mention}** found the word: **{game.secret.upper()}**!"
        embed.add_field(name="Final Result", value=f"{pattern} {guess_word.upper()}")
        embed.add_field(name="Keyboard Status", value=keypad_status, inline=False)
    elif game_over:
        embed = discord.Embed(title="💀 GAME OVER", color=discord.Color.red())
        embed.description = f"The word was **{game.secret.upper()}**."
        embed.add_field(name="Last Guess", value=f"{pattern} {guess_word.upper()}")
        embed.add_field(name="Keyboard Status", value=keypad_status, inline=False)
    else:
        embed = discord.Embed(title=f"Attempt {game.attempts_used}/6", color=discord.Color.gold())
        embed.description = f"**{interaction.user.display_name}** guessed `{guess_word.upper()}`"
        
        embed.add_field(
            name="Guess Result", 
            value=f"{pattern} {guess_word.upper()}{hint_message}", 
            inline=False
        )
        embed.add_field(name="Keyboard Status", value=keypad_status, inline=False)
        embed.set_footer(text=f"{6 - game.attempts_used} tries left!")
        
    await interaction.response.send_message(embed=embed)

    if game_over or win:
        # LEADERBOARD UPDATE - NOW REQUIRES GUILD_ID
        guild_id = interaction.guild_id
        update_leaderboard(bot, interaction.user.id, guild_id, win) 
        bot.games.pop(cid, None)


@bot.tree.command(name="leaderboard", description="Displays the top Wordle players on the server.")
async def leaderboard(interaction: discord.Interaction):
    guild_id = interaction.guild_id 
    
    bot.db_cursor.execute("""
        SELECT user_id, wins, total_games 
        FROM scores 
        WHERE guild_id = ?
        ORDER BY wins DESC, total_games ASC 
        LIMIT 10
    """, (guild_id,))
    results = bot.db_cursor.fetchall()

    if not results:
        embed = discord.Embed(
            title="🥇 Server Wordle Leaderboard",
            description="No games have been finished yet on this server! Be the first to win.",
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed)
        return

    rankings = []
    for i, (user_id, wins, games) in enumerate(results, 1):
        try:
            user = await bot.fetch_user(user_id)
            username = user.display_name
        except discord.NotFound:
            username = f"Unknown User ({user_id})"

        win_rate = (wins / games) * 100 if games > 0 else 0
        
        if i == 1:
            emoji = "🥇"
        elif i == 2:
            emoji = "🥈"
        elif i == 3:
            emoji = "🥉"
        else:
            emoji = f"{i}."
        
        rankings.append(
            f"{emoji} **{username}**\n"
            f"   > Wins: **{wins}** | Games: {games} | Rate: {win_rate:.1f}%"
        )

    embed = discord.Embed(
        title="🏆 Top Wordle Players on This Server",
        description="\n".join(rankings),
        color=discord.Color.gold()
    )
    embed.set_footer(text=f"Check out your rank with /leaderboard!")
    
    await interaction.response.send_message(embed=embed)


# (Rest of the commands: /wordle_board)
@bot.tree.command(name="wordle_board", description="View history and keyboard status.")
async def board(interaction: discord.Interaction):
    game = bot.games.get(interaction.channel_id)
    if not game:
        return await interaction.response.send_message("❌ No game active.", ephemeral=True)

    history_lines = [f"**{i}.** {x['pattern']} **{x['word'].upper()}**" for i, x in enumerate(game.history, 1)]
    
    embed = discord.Embed(
        title="📊 Current Game Board", 
        description="\n".join(history_lines) or "No guesses yet! Be the first.", 
        color=discord.Color.blurple()
    )
    
    keypad_status = get_markdown_keypad_status(game.used_letters)
    embed.add_field(name="Keyboard Status", value=keypad_status, inline=False)
        
    embed.set_footer(text=f"Attempts Used: {game.attempts_used}/6")
    
    await interaction.response.send_message(embed=embed)


# ========= 6. RUN BOT AND WEB SERVER (Threading Logic) =========
if __name__ == "__main__":
    # 1. Start Flask in a background thread to open the required port (10000)
    server_thread = threading.Thread(target=run_flask_server)
    server_thread.start()
    
    # 2. Start the Discord Bot in the main thread
    bot.run(TOKEN)
