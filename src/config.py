import os
from dotenv import load_dotenv

load_dotenv()

# --- DISCORD CONFIG ---
TOKEN = os.getenv('DISCORD_TOKEN')
APP_ID = os.getenv('APP_ID')

# --- SUPABASE CONFIG ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# --- FILE PATHS ---
DICT_DIR = "data/dictionaries"
SECRET_FILE = f"{DICT_DIR}/answers_common.txt"   # Simple list (Original Wordle)
VALID_FILE = f"{DICT_DIR}/guesses_common.txt"    # Full dictionary (Valid guesses)
CLASSIC_FILE = f"{DICT_DIR}/answers_hard.txt"    # Classic list
FULL_WORDS = f"{DICT_DIR}/puzzles_rush.txt"      # Full dictionary for Constraint Mode (6+ letters)
RUSH_WILD_FILE = f"{DICT_DIR}/guesses_rush_wild.txt" # Wild 5-letter words for validation only

BANNED_USERS_FILE = "/etc/secrets/ban.txt" # Secret path for Render/Docker
if not os.path.exists(BANNED_USERS_FILE):
    BANNED_USERS_FILE = f"banned_users.txt" # Local fallback (can stay here or move later)

# --- GAME CONSTANTS ---
KEYBOARD_LAYOUT = ["QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM"]

# --- RANKING & PROGRESSION ---

# XP REQUIREMENTS (Per Level)
XP_LEVELS = {
    10: 100,  # 1-10
    30: 200,  # 11-30
    60: 350,  # 31-60
    1000: 500 # 61+
}

# XP GAINS
XP_GAINS = {
    'SOLO': {'win': 40, 'loss': 5},
    'MULTI': {
        'win': 50, 'correct_4': 40, 'correct_3': 30, 'correct_2': 20, 'correct_1': 10, 'participation': 5
    },
    'BONUS': {'under_60s': 10, 'under_90s': 5}
}

# MATCH PERFORMANCE SCORE (MPS) - WR Calculation
MPS_BASE = {
    'win': 60, 'correct_4': 35, 'correct_3': 25, 'correct_2': 10, 'correct_1': 5, 'participation': 2
}
MPS_EFFICIENCY = { # Bonus for Nth guess win
    1: 50, 2: 40, 3: 30, 4: 20, 5: 10, 6: 5
}
MPS_SPEED = { # Bonus for correct solve time
    60: 20, 90: 10
}

# TIERS (Includes multipliers for reward reduction)
# "at challenger, 20% reward reduction, next tier, another 10%... and so on"
# Challenger: 0.8x, Elite: 0.7x, Master: 0.6x, Grandmaster: 0.5x, Legend: 0.4x
TIERS = [
    {"name": "Legend",      "icon": "legend_tier", "min_wr": 3900, "multiplier": 0.40, "req": "Top 1%"},
    {"name": "Grandmaster", "icon": "💎", "min_wr": 2800, "multiplier": 0.50, "req": "60% WR, <3.9 Avg"},
    {"name": "Master",      "icon": "⚜️", "min_wr": 1600, "multiplier": 0.60, "req": "58% WR, <4.0 Avg"},
    {"name": "Elite",       "icon": "⚔️", "min_wr": 900,  "multiplier": 0.70, "req": "52% WR, <4.4 Avg"},
    {"name": "Challenger",  "icon": "🛡️", "min_wr": 0,    "multiplier": 0.80, "req": "15 Solves"}
]
# ANTI-GRIND SYSTEM
DAILY_CAP_1 = 500  # -50% Reward if > 500 Daily WR
DAILY_CAP_2 = 700  # -50% Additional (Total 75% drop) if > 700 Daily WR



# --- LINKS & ASSETS ---
TOP_GG_LINK = "https://top.gg/bot/1446184470251048991"

# --- ACTIVITIES ---
import discord # Needed for ActivityType
ROTATING_ACTIVITIES = [
    {"type": discord.ActivityType.playing, "name": "/wordle to begin, /guess to play"}, 
    {"type": discord.ActivityType.watching, "name": "users guess five-letter words"},
    {"type": discord.ActivityType.playing, "name": "octopus knows secrets"},
    {"type": discord.ActivityType.watching, "name": "the dictionary for secret words"},
    {"type": discord.ActivityType.listening, "name": "to /help requests"},
    {"type": discord.ActivityType.playing, "name": "with the alphabet"},
    {"type": discord.ActivityType.watching, "name": "did you summon Duck yet?"},
    {"type": discord.ActivityType.playing, "name": "Hard Mode: /wordle_classic"},
    {"type": discord.ActivityType.listening, "name": "to the bot dev rage"},
    {"type": discord.ActivityType.playing, "name": "Guess the word! /guess"},
    {"type": discord.ActivityType.listening, "name": "i rate you 6/6 ⸜(｡˃ ᵕ ˂ )⸝♡"},
    {"type": discord.ActivityType.listening, "name": "snipe your friend at 6/6!"},
    {"type": discord.ActivityType.listening, "name": "⚠️ SYABU not in dictionary"},
    {"type": discord.ActivityType.watching, "name": "staring at green squares like it's art"},
    {"type": discord.ActivityType.playing, "name": "consulting the Wordle gods (they're on break)"},
    {"type": discord.ActivityType.listening, "name": "humming the 6/6 victory anthem"},
    {"type": discord.ActivityType.playing, "name": "rolling for a valid guess... not that one"},
    {"type": discord.ActivityType.watching, "name": "the board as you type 'almost' every time"},
    {"type": discord.ActivityType.listening, "name": "the soft wail of a wasted first guess"},
    {"type": discord.ActivityType.playing, "name": "Hard Mode: sacrifice a vowel"},
    {"type": discord.ActivityType.listening, "name": "Duck whispers: 'Try a different vowel'"},
    {"type": discord.ActivityType.watching, "name": "an octopus rearranging your letters"},
    {"type": discord.ActivityType.playing, "name": "blaming the dictionary since 1604"},
    {"type": discord.ActivityType.listening, "name": "cheering silently for your 5th guess"},
    {"type": discord.ActivityType.playing, "name": "collecting green squares like trophies"},
]
