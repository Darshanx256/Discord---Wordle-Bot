import requests
import random
from src.config import TOKEN, APP_ID, TIERS, C_GAMES, C_WINRATE

def load_app_emojis(bot_token=TOKEN, app_id=APP_ID):
    url = f"https://discord.com/api/v10/applications/{app_id}/emojis"
    headers = {"Authorization": f"Bot {bot_token}"}
    try:
        data = requests.get(url, headers=headers).json()
        if "items" not in data:
            print(f"⚠️ Warning: Could not load emojis. Response: {data}")
            return {}
    except Exception as e:
        print(f"⚠️ Error loading emojis: {e}")
        return {}

    E = {}

    for e in data["items"]:
        raw = e["name"]                  # keep original case for ID
        raw_lower = raw.lower()          # for parsing
        eid = e["id"]
        is_anim = e.get("animated", False)
        prefix = "a" if is_anim else ""

        # final Discord emoji token
        token = f"<{prefix}:{raw}:{eid}>"

        # 1) KEYBOARD FORMAT—kbd_A_correct_green
        if raw_lower.startswith("kbd_"):
            parts = raw.split("_")
            if len(parts) >= 3:
                letter = parts[1].lower()
                state  = parts[2].lower()
                key = f"{letter}_{state}"
                E[key] = token
            continue

        # 2) WORDLE BLOCK FORMAT—green_A / yellow_A / white_A
        if raw_lower.startswith(("green_", "yellow_", "white_")):
            parts = raw.split("_")
            if len(parts) >= 2:
                color, letter = parts
                color = color.lower()
                letter = letter.lower()
                key = f"block_{letter}_{color}"
                E[key] = token
            continue

    return E

# Helper to load emojis once
EMOJIS = load_app_emojis()

def calculate_score(wins, games):
    if games == 0: return 0
    # Simple average for legacy display if needed
    return (wins / games) * 100

def get_tier_display(perc):
    # Legacy percentile tier
    return "🛡️", "Challenger"

def get_win_flavor(attempts):
    flavors = {
        1: "🤯 INSANE!",
        2: "🤩 Amazing!",
        3: "🔥 Great!",
        4: "👍 Good",
        5: "😅 Phew...",
        6: "😬 Close one!"
    }
    return flavors.get(attempts, "")

def calculate_level(xp: int) -> int:
    """Calculates level from total XP based on config thresholds."""
    # Levels 1-10: 100 XP each (1000 total)
    # Levels 11-30: 200 XP each (4000 total -> 5000 cumulative)
    # Levels 31-60: 350 XP each (10500 total -> 15500 cumulative)
    # Levels 61+: 500 XP each
    
    lvl = 1
    curr = xp
    
    # Chunk 1: 1-10 (10 levels * 100)
    if curr >= 1000:
        lvl += 10
        curr -= 1000
    else:
        return lvl + (curr // 100)

    # Chunk 2: 11-30 (20 levels * 200)
    if curr >= 4000:
        lvl += 20
        curr -= 4000
    else:
        return lvl + (curr // 200)
        
    # Chunk 3: 31-60 (30 levels * 350)
    if curr >= 10500:
        lvl += 30
        curr -= 10500
    else:
        return lvl + (curr // 350)
        
    # Chunk 4: 61+ (500 each)
    return lvl + (curr // 500)
