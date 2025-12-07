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

# TIER CONFIGURATION (Text-based Ranks)
# Threshold (0-1), Icon, Name
TIERS = [
    (0.90, "【Ｓ】", "Master"), 
    (0.65, "【Ａ】", "Elite"),      
    (0.40, "【Ｂ】", "Challenger"),       
    (0.00, "【Ｃ】", "Beginner")   
]

C_GAMES = 10  # Bayesian constant (Games)
C_WINRATE = 0.40 # Bayesian constant (Win Rate)

# ========= 2. UTILITY FUNCTIONS =========

def get_keyboard_visual(used_letters: dict) -> str:
    """Generates a clean, text-based keyboard visualization."""
    lines = []
    for row in KEYBOARD_LAYOUT:
        r_line = ""
        for char in row:
            c = char.lower()
            if c in used_letters['correct']: r_line += f"**{char}** "   # Bold
            elif c in used_letters['present']: r_line += f"__{char}__ " # Underline
            elif c in used_letters['absent']: r_line += f"~~{char}~~ " # Strike
            else: r_line += f"{char} "                                  # Normal
        lines.append(r_line.strip())
    
    # Visual indentation for QWERTY look
    lines[1] = u"\u2007" + lines[1]
    lines[2] = u"\u2007\u2007" + lines[2]
    return "\n".join(lines)

def calculate_score(wins, games):
    if games == 0: return 0.0
    return (wins + (C_GAMES * C_WINRATE)) / (games + C_GAMES)

def get_tier_info(percentile):
    for thresh, icon, name in TIERS:
        if percentile >= thresh: return icon, name
    return "【Ｃ】", "Challenger"

def update_db_score(bot, user_id, guild_id, won):
    if not guild_id: return
    bot.db_cursor.execute("SELECT wins, total_games FROM scores WHERE user_id = ? AND guild_id = ?", (user_id, guild_id))
    row = bot.db_cursor.fetchone()
    cur_w, cur_g = row if row else (0, 0)
    new_w = cur_w + 1 if won else cur_w
    bot.db_cursor.execute("INSERT OR REPLACE INTO scores (user_id, guild_id, wins, total_games) VALUES (?, ?, ?, ?)", 
                          (user_id, guild_id, new_w, cur_g + 1))
    bot.db_conn.commit()

def get_user_rank_display(bot, user_id, guild_id):
    """Efficiently determines user rank string."""
    # Local Rank
    bot.db_cursor.execute("SELECT wins, total_games FROM scores WHERE user_id = ? AND guild_id = ?", (user_id, guild_id))
    row = bot.db_cursor.fetchone()
    if not row: return "Unranked"
    
    my_s = calculate_score(row[0], row[1])
    bot.db_cursor.execute("SELECT wins, total_games FROM scores WHERE guild_id = ?", (guild_id,))
    all_s = sorted([calculate_score(r[0], r[1]) for r in bot.db_cursor.fetchall()])
    
    rank_idx = sum(1 for s in all_s if s < my_s)
    perc = rank_idx / len(all_s) if all_s else 0
    icon, _ = get_tier_info(perc)
    return icon

def get_next_secret(bot, guild_id):
    bot.db_cursor.execute("SELECT word FROM guild_history WHERE guild_id = ?", (guild_id,))
    used = {r[0] for r in bot.db_cursor.fetchall()}
    avail = [w for w in bot.secrets if w not in used]
    if not avail:
        bot.db_cursor.execute("DELETE FROM guild_history WHERE guild_id = ?", (guild_id,))
        bot.db_conn.commit()
        avail = bot.secrets
    pick = random.choice(avail)
    bot.db_cursor.execute("INSERT INTO guild_history (guild_id, word) VALUES (?, ?)", (guild_id, pick))
    bot.db_conn.commit()
    return pick

# ========= 3. GAME LOGIC =========

class WordleGame:
    __slots__ = ('secret', 'channel_id', 'started_by', 'history', 'used_letters', 
                 'participants', 'guessed_words', 'last_interaction', 'message_id')

    def __init__(self, secret, channel_id, started_by):
        self.secret = secret
        self.channel_id = channel_id
        self.started_by = started_by
        self.history = []
        self.participants = set()
        self.guessed_words = set()
        self.used_letters = {'correct': set(), 'present': set(), 'absent': set()}
        self.last_interaction = datetime.datetime.now()
        self.message_id = None # Track the main dashboard message

    @property
    def attempts(self): return len(self.history)
    @property
    def is_over(self): return self.attempts >= 6 or (self.history and self.history[-1]['correct'])

    def process_guess(self, guess, user):
        self.last_interaction = datetime.datetime.now()
        
        s_list = list(self.secret); g_list = list(guess)
        res = ["⬜"] * 5; cur_abs = set(guess) - set(s_list)

        # Greens
        for i in range(5):
            if g_list[i] == s_list[i]:
                res[i] = "🟩"; s_list[i] = None; g_list[i] = None
                self.used_letters['correct'].add(guess[i])
                self.used_letters['present'].discard(guess[i])
        
        # Yellows
        for i in range(5):
            if res[i] == "🟩": continue
            ch = g_list[i]
            if ch is not None and ch in s_list:
                res[i] = "🟨"; s_list[s_list.index(ch)] = None
                if ch not in self.used_letters['correct']: self.used_letters['present'].add(ch)
            elif ch is not None: cur_abs.add(ch)

        self.used_letters['absent'].update(cur_abs - self.used_letters['correct'] - self.used_letters['present'])
        
        pat_str = "".join(res)
        is_win = (guess == self.secret)
        self.history.append({'word': guess, 'pattern': pat_str, 'user': user, 'correct': is_win})
        self.participants.add(user.id)
        self.guessed_words.add(guess)
        return is_win

# ========= 4. UI MODALS & VIEWS (The Mind-Blowing Part) =========

class GuessModal(ui.Modal, title="Enter Your Guess"):
    word_input = ui.TextInput(label="5-Letter Word", placeholder="e.g. APPLE", min_length=5, max_length=5)

    def __init__(self, bot, game_view):
        super().__init__()
        self.bot = bot
        self.view = game_view

    async def on_submit(self, interaction: discord.Interaction):
        # 1. Validation
        guess = self.word_input.value.lower().strip()
        cid = interaction.channel_id
        game = self.bot.games.get(cid)

        if not game:
            return await interaction.response.send_message("❌ Game is over or expired.", ephemeral=True)
        
        if not guess.isalpha():
            return await interaction.response.send_message("❌ Letters only.", ephemeral=True)
        if guess in game.guessed_words:
            return await interaction.response.send_message(f"⚠️ **{guess.upper()}** already used!", ephemeral=True)
        if guess not in self.bot.valid_set:
            return await interaction.response.send_message(f"❌ **{guess.upper()}** not in dictionary.", ephemeral=True)

        # 2. Process
        won = game.process_guess(guess, interaction.user)
        
        # 3. Update UI
        embed = self.view.generate_embed(game, interaction.guild)
        
        if game.is_over:
            # Handle End Game
            self.view.clear_items() # Remove buttons
            winner_id = interaction.user.id if won else None
            for pid in game.participants:
                update_db_score(self.bot, pid, interaction.guild_id, (pid == winner_id))
            self.bot.games.pop(cid, None)
            await interaction.response.edit_message(embed=embed, view=None)
        else:
            await interaction.response.edit_message(embed=embed, view=self.view)


class GameDashboardView(ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None) # Persistent view logic handled by bot cache
        self.bot = bot

    def generate_embed(self, game, guild):
        # Dynamic Header
        status = "🟢 Active"
        color = discord.Color.blue()
        
        if game.is_over:
            if game.history[-1]['correct']:
                status = "🏆 VICTORY"
                color = discord.Color.green()
            else:
                status = "💀 DEFEAT"
                color = discord.Color.red()

        embed = discord.Embed(title=f"Wordle  |  {status}", color=color)
        
        # Board Generation
        board_text = ""
        for turn in game.history:
            # Use Rank Icon instead of plain text if possible
            rank = get_user_rank_display(self.bot, turn['user'].id, guild.id)
            board_text += f"`{turn['word'].upper()}` {turn['pattern']} {rank} **{turn['user'].display_name}**\n"
        
        # Fill remaining slots
        attempts_left = 6 - len(game.history)
        if attempts_left > 0 and not game.is_over:
            board_text += ("`_____` ⬜⬜⬜⬜⬜\n" * attempts_left)

        embed.description = board_text
        
        # Footer / Keyboard
        kb_visual = get_keyboard_visual(game.used_letters)
        embed.add_field(name="Keyboard", value=kb_visual, inline=False)
        
        # Progress Bar
        bar = "🟩" * (6 - attempts_left) + "⬛" * attempts_left
        embed.set_footer(text=f"Turn {len(game.history)+1}/6  {bar}  •  Mini: {game.history[-1]['pattern'] if game.history else 'Start'}")
        
        if game.is_over and not game.history[-1]['correct']:
             embed.add_field(name="Secret Word", value=f"||**{game.secret.upper()}**||", inline=False)
        
        return embed

    @ui.button(label="📝 Make a Guess", style=discord.ButtonStyle.primary)
    async def guess_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(GuessModal(self.bot, self))

    @ui.button(label="🛑 End", style=discord.ButtonStyle.danger)
    async def stop_button(self, interaction: discord.Interaction, button: ui.Button):
        cid = interaction.channel_id
        game = self.bot.games.get(cid)
        if not game: return
        
        # Perms Check
        if interaction.user == game.started_by or interaction.permissions.manage_messages:
            self.bot.games.pop(cid, None)
            embed = discord.Embed(title="🛑 Game Stopped", description=f"Word was **{game.secret.upper()}**", color=discord.Color.dark_grey())
            await interaction.response.edit_message(embed=embed, view=None)
        else:
            await interaction.response.send_message("❌ Only Admin/Starter can stop.", ephemeral=True)


# ========= 5. BOT SETUP =========

class WordleBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents(guilds=True))
        self.games = {}; self.secrets = []; self.valid_set = set() 
        self.db_conn = None; self.db_cursor = None

    async def setup_hook(self):
        self.load_data(); self.setup_db()
        await self.tree.sync()
        self.cleanup.start()
        print(f"✅ Ready! {len(self.secrets)} words.")
        
    async def close(self):
        if self.db_conn: self.db_conn.close()
        await super().close()

    def load_data(self):
        if os.path.exists(SECRET_FILE):
            with open(SECRET_FILE, "r") as f: self.secrets = [w.strip().lower() for w in f if len(w.strip())==5]
        if os.path.exists(VALID_FILE):
            with open(VALID_FILE, "r") as f: self.valid_set = {w.strip().lower() for w in f if len(w.strip())==5}
        self.valid_set.update(self.secrets)

    def setup_db(self):
        self.db_conn = sqlite3.connect(DB_NAME, check_same_thread=False)
        self.db_cursor = self.db_conn.cursor()
        self.db_cursor.execute("CREATE TABLE IF NOT EXISTS scores (user_id INTEGER, guild_id INTEGER, wins INTEGER, total_games INTEGER, PRIMARY KEY (user_id, guild_id))")
        self.db_cursor.execute("CREATE TABLE IF NOT EXISTS guild_history (guild_id INTEGER, word TEXT)")
        self.db_conn.commit()

    @tasks.loop(hours=1)
    async def cleanup(self):
        now = datetime.datetime.now()
        rem = [cid for cid, g in self.games.items() if (now - g.last_interaction).total_seconds() > 86400]
        for cid in rem: self.games.pop(cid, None)

bot = WordleBot()

# ========= 6. COMMANDS =========

@bot.tree.command(name="wordle", description="Start a new game console.")
async def wordle(interaction: discord.Interaction):
    if not interaction.guild: return
    cid = interaction.channel_id
    if cid in bot.games: bot.games.pop(cid, None)

    if not bot.secrets: return await interaction.response.send_message("❌ Database error.", ephemeral=True)

    secret = get_next_secret(bot, interaction.guild_id)
    game = WordleGame(secret, cid, interaction.user)
    bot.games[cid] = game
    
    view = GameDashboardView(bot)
    embed = view.generate_embed(game, interaction.guild)
    
    await interaction.response.send_message(embed=embed, view=view)
    msg = await interaction.original_response()
    game.message_id = msg.id


@bot.tree.command(name="leaderboard", description="Server Standings.")
async def leaderboard(interaction: discord.Interaction):
    bot.db_cursor.execute("SELECT user_id, wins, total_games FROM scores WHERE guild_id = ?", (interaction.guild_id,))
    rows = bot.db_cursor.fetchall()
    if not rows: return await interaction.response.send_message("No data.", ephemeral=True)
    
    # Calculate Ranks locally
    data = []
    for uid, w, g in rows:
        data.append((uid, w, g, calculate_score(w, g)))
    data.sort(key=lambda x: x[3], reverse=True)
    
    desc = []
    for i, (uid, w, g, s) in enumerate(data[:10], 1):
        # Optimization: Try cache first
        mem = interaction.guild.get_member(uid)
        name = mem.display_name if mem else f"User {uid}"
        
        # Rank Icon
        perc = (len(data) - (i-1)) / len(data)
        icon, tier_name = get_tier_info(perc)
        
        desc.append(f"`#{i}` {icon} **{name}**\n└ Score: {s*100:.0f} • {w}/{g} Wins")

    embed = discord.Embed(title=f"🏆 {interaction.guild.name} Leaderboard", description="\n".join(desc), color=discord.Color.gold())
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="profile", description="Your stats.")
async def profile(interaction: discord.Interaction):
    uid = interaction.user.id
    gid = interaction.guild_id
    
    rank_icon = get_user_rank_display(bot, uid, gid)
    
    bot.db_cursor.execute("SELECT wins, total_games FROM scores WHERE user_id = ? AND guild_id = ?", (uid, gid))
    row = bot.db_cursor.fetchone()
    w, g = row if row else (0,0)
    
    embed = discord.Embed(title=f"👤 {interaction.user.display_name}", color=discord.Color.teal())
    embed.add_field(name="Rank", value=f"{rank_icon}", inline=True)
    embed.add_field(name="Stats", value=f"Wins: {w}\nGames: {g}", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# --- FLASK & RUN ---
def flask_thread():
    app = Flask(__name__)
    @app.route('/')
    def h(): return "OK", 200
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)), debug=False)

if __name__ == "__main__":
    threading.Thread(target=flask_thread).start()
    bot.run(TOKEN)
