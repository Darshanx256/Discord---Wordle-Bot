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
SECRET_FILE = "words.txt" # Simple list (Original Wordle)
VALID_FILE = "all_words.txt" # Full dictionary (Valid guesses)
CLASSIC_FILE = "words_hard.txt" # Classic list

# --- GAME CONSTANTS ---
KEYBOARD_LAYOUT = ["QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM"]

# --- RANKING & TIERS ---
C_GAMES = 10  # Bayesian constant (Games)
C_WINRATE = 0.40 # Bayesian constant (Win Rate)

TIERS = [
    (0.90, "💎", "Grandmaster"), 
    (0.65, "⚜️", "Master"),      
    (0.40, "⚔️", "Elite"),      
    (0.00, "🛡️", "Challenger")    
]

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
]
