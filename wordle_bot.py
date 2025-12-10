import os
import random
import discord
from discord.ext import commands, tasks
from discord import ui
from dotenv import load_dotenv
import threading
from flask import Flask, send_from_directory, render_template
import asyncio
import sys
import datetime
from supabase import create_client, Client
import requests

# --- 0. EMOJI PREREQUISITES ---
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
APP_ID = os.getenv('APP_ID')
def load_app_emojis(bot_token = TOKEN, app_id = APP_ID):
    url = f"https://discord.com/api/v10/applications/{app_id}/emojis"
    headers = {"Authorization": f"Bot {bot_token}"}
    data = requests.get(url, headers=headers).json()

    E = {}

    for e in data["items"]:
        raw = e["name"]                  # keep original case for ID
        raw_lower = raw.lower()          # for parsing
        eid = e["id"]
        is_anim = e.get("animated", False)
        prefix = "a" if is_anim else ""

        # final Discord emoji token
        token = f"<{prefix}:{raw}:{eid}>"

        # =====================================================
        # 1) KEYBOARD FORMAT—kbd_A_correct_green
        # =====================================================
        if raw_lower.startswith("kbd_"):
            # ex: kbd_A_correct_green -> ["kbd", "A", "correct", "green"]
            parts = raw.split("_")
            letter = parts[1].lower()            # "A" → "a"
            state  = parts[2].lower()            # "correct"
            key = f"{letter}_{state}"            # "a_correct"
            E[key] = token
            continue

        # =====================================================
        # 2) WORDLE BLOCK FORMAT—green_A / yellow_A / white_A
        # =====================================================
        if raw_lower.startswith(("green_", "yellow_", "white_")):
            # ex: green_A → green, A
            color, letter = raw.split("_")       # letter still uppercase
            color = color.lower()
            letter = letter.lower()
            key = f"block_{letter}_{color}"      # block_a_green
            E[key] = token
            continue

        # ignore anything else

    return E

# ---- call it before main program ----
EMOJIS = load_app_emojis(TOKEN, APP_ID)

# --- 1. CONFIGURATION ---
# NEW Environment variables for Supabase Client
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not TOKEN: 
    print("❌ FATAL: DISCORD_TOKEN not found.")
    exit(1)

if not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ FATAL: SUPABASE_URL or SUPABASE_KEY (for Supabase client) not found.")
    exit(1)

SECRET_FILE = "words.txt" # Simple list (Original Wordle)
VALID_FILE = "all_words.txt" # Full dictionary (Valid guesses)
CLASSIC_FILE = "words_hard.txt" # Classic list
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
    # --- EASTER EGG ---
    extra_line = ""
    if random.randint(1, 50) == 1:
        extra_line = (
            "\n"
            "> **🎉 RARE DUCK OF LUCK SUMMONED! 🎉**\n"
            "> 🦆 You found the Duck of Luck! Have a nice day!"
        )
    # ------------------

    output_lines = []
    for row in KEYBOARD_LAYOUT:
        line = ""
        for char_key in row:
            c = char_key.lower()
            if c in used_letters['correct']:
                state = "correct"
            elif c in used_letters['misplaced']:
                state = "misplaced"
            elif c in used_letters['absent']:
                state = "absent"
            else:
                state = "unknown"

            # SAFETY CHECK: If the emoji key doesn't exist, fallback to text
            emoji_key = f"{c}_{state}"
            if emoji_key in EMOJIS:
                line += EMOJIS[emoji_key] + " "
            else:
                line += f"` {char_key} ` " 
                
        output_lines.append(line.strip())

    # Add indentation for QWERTY look
    output_lines[1] = u"\u2007" + output_lines[1]
    output_lines[2] = u"\u2007\u2007" + output_lines[2]
    
    keypad_display = "\n".join(output_lines)

    # Safety check for Legend keys too
    legend_a_corr = EMOJIS.get('a_correct', '🟩')
    legend_a_mis = EMOJIS.get('a_misplaced', '🟨')
    legend_a_abs = EMOJIS.get('a_absent', '⬛')

    legend = (
        "\n\n**Legend:**\n"
        f"{legend_a_corr} = Correct | "
        f"{legend_a_mis} = Misplaced | "
        f"{legend_a_abs} = Absent\n"
    )
    return keypad_display + extra_line + legend
    
def update_leaderboard(bot: commands.Bot, user_id: int, guild_id: int, won_game: bool):
    """Updates score using the Supabase client's upsert method."""
    if not guild_id: return 

    try:
        # 1. Fetch current score
        response = bot.supabase_client.table('scores') \
            .select('wins, total_games') \
            .eq('user_id', user_id) \
            .eq('guild_id', guild_id) \
            .execute()
        
        data = response.data
        
        cur_w, cur_g = (data[0]['wins'], data[0]['total_games']) if data else (0, 0)
        
        # 2. Calculate new scores
        new_w = cur_w + 1 if won_game else cur_w
        new_g = cur_g + 1

        # 3. UPSERT (Insert or Update) the score
        score_data = {
            'user_id': user_id, 
            'guild_id': guild_id, 
            'wins': new_w, 
            'total_games': new_g
        }
        
        bot.supabase_client.table('scores').upsert(score_data).execute()

    except Exception as e:
        print(f"DB ERROR (General) in update_leaderboard: {e}")

def get_next_secret(bot: commands.Bot, guild_id: int) -> str:
    """Gets a secret word from the simple pool (bot.secrets) using guild_history table."""
    
    try:
        # 1. SELECT used words
        response = bot.supabase_client.table('guild_history') \
            .select('word') \
            .eq('guild_id', guild_id) \
            .execute()
        
        used_words = {r['word'] for r in response.data}
        available_words = [w for w in bot.secrets if w not in used_words]
        
        if not available_words:
            # 2. Reset history
            bot.supabase_client.table('guild_history') \
                .delete() \
                .eq('guild_id', guild_id) \
                .execute()
                
            available_words = bot.secrets
            print(f"🔄 Guild {guild_id} history reset for Simple mode. Word pool recycled.")
            
        pick = random.choice(available_words)
        
        # 3. INSERT new secret
        bot.supabase_client.table('guild_history') \
            .insert({'guild_id': guild_id, 'word': pick}) \
            .execute()
            
        return pick
    
    except Exception as e:
        print(f"DB ERROR in get_next_secret: {e}")
        print("CRITICAL: Falling back to random word (Simple) due to DB failure.")
        return random.choice(bot.secrets)
        
def get_next_classic_secret(bot: commands.Bot, guild_id: int) -> str:
    """Gets a secret word from the hard pool (bot.hard_secrets) using guild_history_classic table."""
    
    try:
        # 1. SELECT used words from the classic table
        response = bot.supabase_client.table('guild_history_classic') \
            .select('word') \
            .eq('guild_id', guild_id) \
            .execute()
        
        used_words = {r['word'] for r in response.data}
        available_words = [w for w in bot.hard_secrets if w not in used_words]
        
        if not available_words:
            # 2. Reset history
            bot.supabase_client.table('guild_history_classic') \
                .delete() \
                .eq('guild_id', guild_id) \
                .execute()
                
            available_words = bot.hard_secrets
            print(f"🔄 Guild {guild_id} history reset for Classic mode. Word pool recycled.")
            
        pick = random.choice(available_words)
        
        # 3. INSERT new secret
        bot.supabase_client.table('guild_history_classic') \
            .insert({'guild_id': guild_id, 'word': pick}) \
            .execute()
            
        return pick
    
    except Exception as e:
        print(f"DB ERROR (General) in get_next_classic_secret: {e}")
        print("CRITICAL: Falling back to random word (Classic) due to DB failure.")
        return random.choice(bot.hard_secrets)


def get_win_flavor(attempts: int) -> str:
    """Returns a fun message based on how quickly they won."""
    if attempts == 1: return "🤯 IMPOSSIBLE! Pure luck or genius?"
    if attempts == 2: return "🔥 Insane! You read my mind."
    if attempts == 3: return "⚡ Blazing fast! Great job."
    if attempts == 4: return "👏 Solid performance."
    if attempts == 5: return "😅 Cutting it close..."
    return "💀 CLUTCH! That was stressful."

# --- PAGINATION VIEW CLASS  ---
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
                
                description_lines.append(f"{medal} {tier_icon} **{name}**\n   > Score: **{score:.2f}** | Wins: {w} | Games: {g}")

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
    # Initialize Flask App
    app = Flask(__name__, static_folder='static')

    # --- ROUTE HANDLERS ---
    
    # 1. Homepage Route (Serving index.html)
    @app.route('/')
    def home():
        # Serves the index.html file from the 'static' folder
        return send_from_directory(app.static_folder, 'index.html')

    # 2. Terms of Service Route (Serving tos.html)
    @app.route('/terms')
    def terms():
        # Serves the tos.html file from the 'static' folder
        return send_from_directory(app.static_folder, 'tos.html')

    # 3. Privacy Policy Route (Serving privacy.html)
    @app.route('/privacy')
    def privacy():
        # Serves the privacy.html file from the 'static' folder
        return send_from_directory(app.static_folder, 'privacy.html')

    @app.route('/favicon.ico')
    def favicon():
        return send_from_directory(app.static_folder, 'favicon.ico')

    @app.route('/icon.png')
    def icon():
        return send_from_directory(app.static_folder, 'icon.png')

    # --- SERVER RUN CONFIGURATION ---
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)


# ========= 4. GAME CLASS =========
class WordleGame:
    __slots__ = ('secret', 'secret_set', 'channel_id', 'started_by', 'max_attempts', 'history', 
                 'used_letters', 'participants', 'guessed_words', 'last_interaction', 'message_id')

    def __init__(self, secret: str, channel_id: int, started_by: discord.abc.User, message_id: int):
        self.secret = secret
        self.secret_set = set(secret) # Store secret as a set for O(1) lookups
        self.channel_id = channel_id
        self.started_by = started_by
        self.max_attempts = 6
        self.history = [] 
        self.participants = set() 
        self.guessed_words = set()
        self.used_letters = {'correct': set(), 'misplaced': set(), 'absent': set()}
        self.last_interaction = datetime.datetime.now()
        self.message_id = message_id

    @property
    def attempts_used(self): return len(self.history)

    def is_duplicate(self, word: str) -> bool: return word in self.guessed_words

    def evaluate_guess(self, guess: str) -> str:
        s_list = list(self.secret)
        g_list = list(guess)

        # default = white block emojis
        res = [EMOJIS[f"block_{ch.lower()}_white"] for ch in guess]

        # 1. Greens (Exact Matches)
        for i in range(5):
            if g_list[i] == s_list[i]:
                letter = guess[i].lower()
                res[i] = EMOJIS[f"block_{letter}_green"]
                s_list[i] = None
                g_list[i] = None
                self.used_letters['correct'].add(letter)
                self.used_letters['misplaced'].discard(letter)

        # 2. Yellows (Misplaced)
        for i in range(5):
            if g_list[i] is None:
                continue

            ch = g_list[i]
            if ch in s_list:
                letter = ch.lower()
                res[i] = EMOJIS[f"block_{letter}_yellow"]
                s_list[s_list.index(ch)] = None

                if letter not in self.used_letters['correct']:
                    self.used_letters['misplaced'].add(letter)

        # 3. Absents (Grey/White)
        self.used_letters['absent'].update(
            set(guess) - self.used_letters['correct'] - self.used_letters['misplaced']
        )

        return "".join(res)

    def process_turn(self, guess: str, user):
        self.last_interaction = datetime.datetime.now()
        pat = self.evaluate_guess(guess)
        
        self.history.append({'word': guess, 'pattern': pat, 'user': user})
        self.participants.add(user.id)
        self.guessed_words.add(guess)
        
        return pat, (guess == self.secret), ((guess == self.secret) or (self.attempts_used >= self.max_attempts))


# ========= 5. BOT SETUP =========
class WordleBot(commands.Bot):
    def __init__(self):
        # ENABLE MEMBERS INTENT for Leaderboards
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True # <--- CRITICAL for get_member()
        
        super().__init__(command_prefix="!", intents=intents)
        self.games = {} 
        self.secrets = []       
        self.hard_secrets = []  
        self.valid_set = set()  
        self.supabase_client: Client = None

    async def setup_hook(self):
        self.load_local_data()
        self.setup_db()
        await self.tree.sync()
        self.cleanup_task.start()
        self.db_ping_task.start()
        # FIX: Changed self.all_secrets to self.hard_secrets
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
                        embed = di        # 1) KEYBOARD FORMAT—kbd_A_correct_green
        # =====================================================
        if raw_lower.startswith("kbd_"):
            # ex: kbd_A_correct_green -> ["kbd", "A", "correct", "green"]
            parts = raw.split("_")
            letter = parts[1].lower()            # "A" → "a"
            state  = parts[2].lower()            # "correct"
            key = f"{letter}_{state}"            # "a_correct"
            E[key] = token
            continue

        # =====================================================
        # 2) WORDLE BLOCK FORMAT—green_A / yellow_A / white_A
        # =====================================================
        if raw_lower.startswith(("green_", "yellow_", "white_")):
            # ex: green_A → green, A
            color, letter = raw.split("_")       # letter still uppercase
            color = color.lower()
            letter = letter.lower()
            key = f"block_{letter}_{color}"      # block_a_green
            E[key] = token
            continue

        # ignore anything else

    return E

# ---- call it before main program ----
EMOJIS = load_app_emojis(TOKEN, APP_ID)

# --- 1. CONFIGURATION ---
# NEW Environment variables for Supabase Client
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not TOKEN: 
    print("❌ FATAL: DISCORD_TOKEN not found.")
    exit(1)

if not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ FATAL: SUPABASE_URL or SUPABASE_KEY (for Supabase client) not found.")
    exit(1)

SECRET_FILE = "words.txt" # Simple list (Original Wordle)
VALID_FILE = "all_words.txt" # Full dictionary (Valid guesses)
CLASSIC_FILE = "words_hard.txt" # Classic list
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
        extra_line = (
            "\n"
            "> **🎉 RARE DUCK OF LUCK SUMMONED! 🎉**\n"
            "> 🦆 CONGRATULATIONS! You summoned a RARE Duck of Luck!\n"
            "> Have a nice day!"
        )
    #egg end

    """Generates the stylized keypad using Discord app emojis."""
    output_lines = []
    for row in KEYBOARD_LAYOUT:
        line = ""
        for char_key in row:
            c = char_key.lower()
            if c in used_letters['correct']:
                state = "correct"
            elif c in used_letters['misplaced']:
                state = "misplaced"
            elif c in used_letters['absent']:
                state = "absent"
            else:
                state = "unknown"
            line += EMOJIS[f"{c}_{state}"] + " "
        output_lines.append(line.strip())

    output_lines[1] = u"\u2007" + output_lines[1]
    output_lines[2] = u"\u2007\u2007" + output_lines[2]
    keypad_display = "\n".join(output_lines)

    legend = (
        "\n\nLegend:\n"
        f"{EMOJIS['a_correct']} = Correct | "
        f"{EMOJIS['a_misplaced']} = Misplaced | "
        f"{EMOJIS['a_absent']} = Absent\n"
    )
    return keypad_display + extra_line + legend

def update_leaderboard(bot: commands.Bot, user_id: int, guild_id: int, won_game: bool):
    """Updates score using the Supabase client's upsert method."""
    if not guild_id: return 

    try:
        # 1. Fetch current score
        response = bot.supabase_client.table('scores') \
            .select('wins, total_games') \
            .eq('user_id', user_id) \
            .eq('guild_id', guild_id) \
            .execute()
        
        data = response.data
        
        cur_w, cur_g = (data[0]['wins'], data[0]['total_games']) if data else (0, 0)
        
        # 2. Calculate new scores
        new_w = cur_w + 1 if won_game else cur_w
        new_g = cur_g + 1

        # 3. UPSERT (Insert or Update) the score
        score_data = {
            'user_id': user_id, 
            'guild_id': guild_id, 
            'wins': new_w, 
            'total_games': new_g
        }
        
        bot.supabase_client.table('scores').upsert(score_data).execute()

    except Exception as e:
        print(f"DB ERROR (General) in update_leaderboard: {e}")

def get_next_secret(bot: commands.Bot, guild_id: int) -> str:
    """Gets a secret word from the simple pool (bot.secrets) using guild_history table."""
    
    try:
        # 1. SELECT used words
        response = bot.supabase_client.table('guild_history') \
            .select('word') \
            .eq('guild_id', guild_id) \
            .execute()
        
        used_words = {r['word'] for r in response.data}
        available_words = [w for w in bot.secrets if w not in used_words]
        
        if not available_words:
            # 2. Reset history
            bot.supabase_client.table('guild_history') \
                .delete() \
                .eq('guild_id', guild_id) \
                .execute()
                
            available_words = bot.secrets
            print(f"🔄 Guild {guild_id} history reset for Simple mode. Word pool recycled.")
            
        pick = random.choice(available_words)
        
        # 3. INSERT new secret
        bot.supabase_client.table('guild_history') \
            .insert({'guild_id': guild_id, 'word': pick}) \
            .execute()
            
        return pick
    
    except Exception as e:
        print(f"DB ERROR in get_next_secret: {e}")
        print("CRITICAL: Falling back to random word (Simple) due to DB failure.")
        return random.choice(bot.secrets)
        
def get_next_classic_secret(bot: commands.Bot, guild_id: int) -> str:
    """Gets a secret word from the hard pool (bot.hard_secrets) using guild_history_classic table."""
    
    try:
        # 1. SELECT used words from the classic table
        response = bot.supabase_client.table('guild_history_classic') \
            .select('word') \
            .eq('guild_id', guild_id) \
            .execute()
        
        used_words = {r['word'] for r in response.data}
        available_words = [w for w in bot.hard_secrets if w not in used_words]
        
        if not available_words:
            # 2. Reset history
            bot.supabase_client.table('guild_history_classic') \
                .delete() \
                .eq('guild_id', guild_id) \
                .execute()
                
            available_words = bot.hard_secrets
            print(f"🔄 Guild {guild_id} history reset for Classic mode. Word pool recycled.")
            
        pick = random.choice(available_words)
        
        # 3. INSERT new secret
        bot.supabase_client.table('guild_history_classic') \
            .insert({'guild_id': guild_id, 'word': pick}) \
            .execute()
            
        return pick
    
    except Exception as e:
        print(f"DB ERROR (General) in get_next_classic_secret: {e}")
        print("CRITICAL: Falling back to random word (Classic) due to DB failure.")
        return random.choice(bot.hard_secrets)


def get_win_flavor(attempts: int) -> str:
    """Returns a fun message based on how quickly they won."""
    if attempts == 1: return "🤯 IMPOSSIBLE! Pure luck or genius?"
    if attempts == 2: return "🔥 Insane! You read my mind."
    if attempts == 3: return "⚡ Blazing fast! Great job."
    if attempts == 4: return "👏 Solid performance."
    if attempts == 5: return "😅 Cutting it close..."
    return "💀 CLUTCH! That was stressful."

# --- PAGINATION VIEW CLASS  ---
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
                
                description_lines.append(f"{medal} {tier_icon} **{name}**\n   > Score: **{score:.2f}** | Wins: {w} | Games: {g}")

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
    # Initialize Flask App
    app = Flask(__name__, static_folder='static')

    # --- ROUTE HANDLERS ---
    
    # 1. Homepage Route (Serving index.html)
    @app.route('/')
    def home():
        # Serves the index.html file from the 'static' folder
        return send_from_directory(app.static_folder, 'index.html')

    # 2. Terms of Service Route (Serving tos.html)
    @app.route('/terms')
    def terms():
        # Serves the tos.html file from the 'static' folder
        return send_from_directory(app.static_folder, 'tos.html')

    # 3. Privacy Policy Route (Serving privacy.html)
    @app.route('/privacy')
    def privacy():
        # Serves the privacy.html file from the 'static' folder
        return send_from_directory(app.static_folder, 'privacy.html')

    @app.route('/favicon.ico')
    def favicon():
        return send_from_directory(app.static_folder, 'favicon.ico')

    @app.route('/icon.png')
    def icon():
        return send_from_directory(app.static_folder, 'icon.png')

    # --- SERVER RUN CONFIGURATION ---
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)


# ========= 4. GAME CLASS =========
class WordleGame:
    __slots__ = ('secret', 'secret_set', 'channel_id', 'started_by', 'max_attempts', 'history', 
                 'used_letters', 'participants', 'guessed_words', 'last_interaction', 'message_id')

    def __init__(self, secret: str, channel_id: int, started_by: discord.abc.User, message_id: int):
        self.secret = secret
        self.secret_set = set(secret) # Store secret as a set for O(1) lookups
        self.channel_id = channel_id
        self.started_by = started_by
        self.max_attempts = 6
        self.history = [] 
        self.participants = set() 
        self.guessed_words = set()
        self.used_letters = {'correct': set(), 'misplaced': set(), 'absent': set()}
        self.last_interaction = datetime.datetime.now()
        self.message_id = message_id

    @property
    def attempts_used(self): return len(self.history)

    def is_duplicate(self, word: str) -> bool: return word in self.guessed_words

    def evaluate_guess(self, guess: str) -> str:
        s_list = list(self.secret)
        g_list = list(guess)
            
        # default = white block emojis
        res = [EMOJIS[f"block_{ch.lower()}_white"] for ch in guess]

        # Track absents early (optional, kept from your version)
        cur_abs = set(guess) - self.secret_set 

        # 1. Greens (Exact Matches)
        for i in range(5):
            if g_list[i] == s_list[i]:
                letter = guess[i].lower()
                res[i] = EMOJIS[f"block_{letter}_green"]
                s_list[i] = None
                g_list[i] = None
                self.used_letters['correct'].add(letter)
                self.used_letters['misplaced'].discard(letter)

        # 2. Yellows (Misplaced)
        for i in range(5):
            if res[i] == EMOJIS.get(f"block_{guess[i].lower()}_green"): 
                continue

            ch = g_list[i]
            if ch is not None and ch in s_list:
                letter = ch.lower()
                res[i] = EMOJIS[f"block_{letter}_yellow"]
                s_list[s_list.index(ch)] = None

                if letter not in self.used_letters['correct']:
                    self.used_letters['misplaced'].add(letter)

        # 3. Absents (Grey/White)
        self.used_letters['absent'].update(
            set(guess) - self.used_letters['correct'] - self.used_letters['misplaced']
        )

        return "".join(res)

    def process_turn(self, guess: str, user):
        self.last_interaction = datetime.datetime.now()
        pat = self.evaluate_guess(guess)
        
        self.history.append({'word': guess, 'pattern': pat, 'user': user})
        self.participants.add(user.id)
        self.guessed_words.add(guess)
        
        return pat, (guess == self.secret), ((guess == self.secret) or (self.attempts_used >= self.max_attempts))


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
        # FIX: Changed self.all_secrets to self.hard_secrets
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

bot = WordleBot()


# ========= 6. EVENTS & COMMANDS =========

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
    
    embed = discord.Embed(title="✨ Wordle Started! (Simple)", color=discord.Color.blue())
    embed.description = "A **simple 5-letter word** has been chosen. **6 attempts** total."
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
    
    embed = discord.Embed(title="⚔️ Wordle Started! (Classic)", color=discord.Color.dark_gold())
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
    if not interaction.guild: return
    cid = interaction.channel_id
    g_word = word.lower().strip()
    game = bot.games.get(cid)

    if not game:
        return await interaction.response.send_message("⚠️ No active game. Start with `/wordle` or `/wordle_classic`.", ephemeral=True)

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
        # The update_leaderboard call is synchronous, running it in a thread to prevent blocking
        for pid in game.participants:
            await asyncio.to_thread(update_leaderboard, bot, pid, interaction.guild_id, (pid == winner_id))
        bot.games.pop(cid, None)

@bot.tree.command(name="wordle_board", description="View current board.")
async def board(interaction: discord.Interaction):
    if not interaction.guild: return
    game = bot.games.get(interaction.channel_id)
    if not game: return await interaction.response.send_message("❌ No active game.", ephemeral=True)
    
    board_display = "\n".join([f"`{h['word'].upper()}` {h['pattern']}" for h in game.history])

    embed = discord.Embed(title="📊 Current Board", description=board_display, color=discord.Color.blurple())
    embed.add_field(name="Keyboard Status", value=get_markdown_keypad_status(game.used_letters), inline=False)
    embed.set_footer(text=f"Attempts: {game.attempts_used}/6")
    await interaction.response.send_message(embed=embed)


# --- HELPER: Process Data for View (Optimized Network) ---
# FIX: Added asyncio.sleep(0.1) after every fetch_user call to prevent Discord rate limits.
async def fetch_and_format_rankings(results, bot_instance, guild=None):
    formatted_data = []
    
    for i, (uid, w, g, s) in enumerate(results):
        
        name = f"User {uid}"
        fetched_successfully = False
        
        if guild:
            # 1. Try Local Cache (FAST & SAFE - No API Call)
            member = guild.get_member(uid)
            if member:
                name = member.display_name
                fetched_successfully = True
        
        if not fetched_successfully:
            # 2. Try Global Cache (FAST & SAFE - No API Call)
            user = bot_instance.get_user(uid)
            if user:
                name = user.display_name
                fetched_successfully = True
        
        if not fetched_successfully:
            # 3. API Call (SLOW & DANGEROUS - Counts towards Rate Limit)
            try: 
                u = await bot_instance.fetch_user(uid)
                name = u.display_name
                
                # --- CRITICAL FIX ---
                await asyncio.sleep(0.1) 
                # --------------------
                
            except: 
                pass
        
        formatted_data.append((i + 1, name, w, g, (w/g)*100 if g > 0 else 0, s))
        
    return formatted_data

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
    # --- Critical Fix: Check if the interaction has already been acknowledged/timed out ---
    if not interaction.response.is_done():
        try:
            # Attempt to defer the response to acknowledge the command
            await interaction.response.defer()
        except discord.errors.NotFound:
            # If defer fails (404 Unknown interaction), the bot cannot proceed 
            # as the token has expired/been used.
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
        # Use followup.send since the interaction was deferred (or attempted to be)
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

import asyncio
import discord
# Assuming 'bot', 'calculate_player_score', 'get_tier_display' are available

# --- NEW: SQL Query for Profile Fetch ---
# This query joins the scores table with the two Materialized Views to get
# all necessary stats (wins, games, score, rank) in a single request for the user.
PROFILE_SQL_QUERY = """
SELECT
    -- Base Score Data (Used as the main source and for initial wins/games count)
    s.wins AS base_guild_wins,
    s.total_games AS base_guild_games,
    
    -- Guild Leaderboard Data (Rank and Score in the current server)
    gld.guild_score,
    gld.guild_rank,
    
    -- Global Leaderboard Data (Aggregated global stats)
    gl.global_wins,
    gl.global_games,
    gl.global_score,
    gl.global_rank
    
FROM
    scores s
LEFT JOIN
    guild_leaderboard gld ON s.user_id = gld.user_id AND s.guild_id = gld.guild_id
LEFT JOIN
    global_leaderboard gl ON s.user_id = gl.user_id
    
WHERE
    s.user_id = %(user_id)s
    AND s.guild_id = %(guild_id)s;
"""

@bot.tree.command(name="profile", description="Check your personal stats.")
async def profile(interaction: discord.Interaction):
    if not interaction.guild: return
    uid = interaction.user.id
    guild_id = interaction.guild_id
    
    await interaction.response.defer()
    
    # 1. FETCH & CALC DATA (Run in Thread)
    def fetch_profile_stats_sync():
        try:
            # Single query for User Score + Joining Views
            # Note: Ensure your Supabase Foreign Keys are set up for this join to work,
            # otherwise, you might need 3 separate queries.
            profile_data = bot.supabase_client.from_('scores').select(
                "wins, total_games, guild_leaderboard(guild_score, guild_rank), global_leaderboard(global_wins, global_games, global_score, global_rank)"
            ).eq('user_id', uid).eq('guild_id', guild_id).maybe_single().execute()
            
            data = profile_data.data if profile_data.data else {}

            # Parse Local Data
            s_wins = data.get('wins', 0)
            s_games = data.get('total_games', 0)
            
            # Safe parsing of nested data (Supabase returns list for joins)
            guild_lb_list = data.get('guild_leaderboard', [])
            guild_lb = guild_lb_list[0] if guild_lb_list else {}
            
            global_lb_list = data.get('global_leaderboard', [])
            g_lb = global_lb_list[0] if global_lb_list else {}

            # Server Stats
            # FIX: Used 'calculate_score' instead of 'calculate_player_score'
            s_score = guild_lb.get('guild_score', calculate_score(s_wins, s_games))
            s_rank_num = guild_lb.get('guild_rank', 'N/A')

            # Global Stats
            g_wins = g_lb.get('global_wins', 0)
            g_games = g_lb.get('global_games', 0)
            g_score = g_lb.get('global_score', calculate_score(g_wins, g_games))
            g_rank_num = g_lb.get('global_rank', 'N/A')
            
            # --- TIER CALCULATION ---
            # To get accurate percentile, we need a rough count or list of all scores.
            # Optimized: Fetch only scores column to save bandwidth
            all_guild_scores = bot.supabase_client.table('guild_leaderboard') \
                .select('guild_score').eq('guild_id', guild_id).execute()
            
            all_global_scores = bot.supabase_client.table('global_leaderboard') \
                .select('global_score').limit(50000).execute() # Cap limit for safety

            server_scores = sorted([r['guild_score'] for r in all_guild_scores.data])
            global_scores_list = sorted([r['global_score'] for r in all_global_scores.data])
            
            # Calculate Tiers
            s_rank_idx = sum(1 for s in server_scores if s < s_score)
            s_perc = s_rank_idx / len(server_scores) if server_scores else 0
            s_tier_icon, s_tier_name = get_tier_display(s_perc)
            
            g_rank_idx = sum(1 for s in global_scores_list if s < g_score)
            g_perc = g_rank_idx / len(global_scores_list) if global_scores_list else 0
            g_tier_icon, g_tier_name = get_tier_display(g_perc)
            
            return (
                s_wins, s_games, s_score, s_tier_icon, s_tier_name, s_rank_num,
                g_wins, g_games, g_score, g_tier_icon, g_tier_name, g_rank_num
            )
        except Exception as e:
            print(f"Profile Fetch Error: {e}")
            return None

    # Run the blocking DB code in a thread
    stats = await asyncio.to_thread(fetch_profile_stats_sync)

    if not stats:
        return await interaction.followup.send("❌ Error fetching profile or no data found.", ephemeral=True)

    # Unpack
    (s_wins, s_games, s_score, s_tier_icon, s_tier_name, s_rank_num,
     g_wins, g_games, g_score, g_tier_icon, g_tier_name, g_rank_num) = stats

    # 2. DISPLAY LOGIC
    embed = discord.Embed(color=discord.Color.teal())
    embed.set_author(name=f"{interaction.user.display_name}'s Profile", icon_url=interaction.user.display_avatar.url)
    
    # Global Field
    g_rank_display = f"**#{g_rank_num}**" if isinstance(g_rank_num, int) else str(g_rank_num)
    embed.add_field(
        name="🌍 **Global Aggregate**", 
        value=(
            f"\u2003🏅 Rank: {g_rank_display}\n"
            f"\u2003🎗️ Tier: {g_tier_icon} **{g_tier_name}**\n"
            f"\u2003📊 Score: **{g_score:.2f}**\n"
            f"\u2003✅ Wins: **{g_wins}**\n"
            f"\u2003🎲 Games: **{g_games}**"
        ), 
        inline=True
    )

    # Server Field
    s_rank_display = f"**#{s_rank_num}**" if isinstance(s_rank_num, int) else str(s_rank_num)
    embed.add_field(
        name=f"🏰 **{interaction.guild.name}**", 
        value=(
            f"\u2003🏅 Rank: {s_rank_display}\n"
            f"\u2003🎗️ Tier: {s_tier_icon} **{s_tier_name}**\n"
            f"\u2003📊 Score: **{s_score:.2f}**\n"
            f"\u2003✅ Wins: **{s_wins}**\n"
            f"\u2003🎲 Games: **{s_games}**"
        ), 
        inline=True
    )
    
    embed.set_footer(text="Keep playing to rank up!", icon_url=bot.user.display_avatar.url)
    
    await interaction.followup.send(embed=embed)

    
if __name__ == "__main__":
    t = threading.Thread(target=run_flask_server)
    t.start()
    bot.run(TOKEN)
