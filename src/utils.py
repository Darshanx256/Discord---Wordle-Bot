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

def calculate_score(wins: int, games: int) -> float:
    """Calculates Bayesian average score for ranking."""
    if games == 0: return 0.0
    # Score = (Wins + Prior_Wins) / (Games + Prior_Games)
    return 10 * ((wins + (C_GAMES * C_WINRATE)) / (games + C_GAMES))

def get_tier_display(percentile: float) -> tuple:
    """Returns the Tier Icon and Name based on percentile rank."""
    for thresh, icon, name in TIERS:
        if percentile >= thresh: return icon, name
    return TIERS[-1][1], TIERS[-1][2] # Default to lowest

def get_win_flavor(attempts: int) -> str:
    """Returns a fun message based on how quickly they won."""
    if attempts == 1: return "🤯 IMPOSSIBLE! Pure luck or genius?"
    if attempts == 2: return "🔥 Insane! You read my mind."
    if attempts == 3: return "⚡ Blazing fast! Great job."
    if attempts == 4: return "👏 Solid performance."
    if attempts == 5: return "😅 Cutting it close..."
    return "💀 CLUTCH! That was stressful."
