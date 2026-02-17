import requests
import random
import asyncio
import discord
from src.config import TOKEN, APP_ID, TIERS

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

        # 3. EASTER EGGS & BADGES & TIERS
        fav_list = [
            "eyes", "duck", "dragon", "candy", 
            "duck_lord_badge", "dragon_slayer_badge", "candy_rush_badge", 
            "legend_tier",
            "unknown", "checkpoint", "fire"
        ]
        if raw_lower in fav_list:
            E[raw_lower] = token
            continue

    return E

# Helper to load emojis once
EMOJIS = load_app_emojis()

def get_badge_emoji(badge_type: str) -> str:
    """Returns the full badge emoji for a given badge type."""
    badge_map = {
        "duck_lord_badge": "duck_lord_badge",
        "dragon_slayer_badge": "dragon_slayer_badge",
        "candy_rush_badge": "candy_rush_badge",
        "dragon": "dragon" # Map the streak milestone ID directly
    }
    if badge_type in badge_map:
        emoji_key = badge_map[badge_type]
        return EMOJIS.get(emoji_key, "")
    return ""

def get_badge_full_display(badge_type: str) -> str:
    """Returns badge emoji + title for /profile display."""
    badge_map = {
        "duck_lord_badge": ("duck_lord_badge", "Duck Lord"),
        "dragon_slayer_badge": ("dragon_slayer_badge", "Dragon Slayer"),
        "candy_rush_badge": ("candy_rush_badge", "Sugar Rush"),
        "dragon": ("dragon", "Dragon Milestone")
    }
    if badge_type in badge_map:
        emoji_key, title = badge_map[badge_type]
        emoji = EMOJIS.get(emoji_key, "")
        return f"{emoji} {title}" if emoji else title
    return ""

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

# --- EASTER EGG DROPS (UNIFORM) ---
EGG_COOLDOWN_SECONDS = 600
EGG_ROLL_MAX = 10000

def roll_easter_egg(is_classic: bool, roll: int | None = None) -> str | None:
    """
    Uniform egg drop logic with a single roll.
    Classic: dragon 0.75% (75/10000), candy 1% (100/10000)
    Simple:  duck   1% (100/10000), candy 1% (100/10000)
    """
    r = roll if roll is not None else random.randint(1, EGG_ROLL_MAX)
    if is_classic:
        if 1 <= r <= 75:
            return "dragon"
        if 76 <= r <= 175:
            return "candy"
        return None
    # Simple pool
    if 1 <= r <= 100:
        return "duck"
    if 101 <= r <= 200:
        return "candy"
    return None

def format_egg_message(egg: str, display_name: str, emojis: dict) -> str:
    egg_emoji = emojis.get(egg, "🎉")
    return f"{egg_emoji} {display_name} • {egg.title()} found • Added to collection"

def format_attempt_footer(attempts_used: int, max_attempts: int) -> str:
    used = max(0, min(attempts_used, max_attempts))
    filled = "•" * used
    empty = "○" * (max_attempts - used)
    return f"Attempt {used}/{max_attempts} [{filled}{empty}]"

def calculate_level(xp: int) -> int:
    """Calculates level from total XP. Legacy simple return."""
    lvl, _, _ = get_level_progress(xp)
    return lvl

def get_level_progress(total_xp: int):
    """Returns (level, xp_in_level, xp_needed_for_next)."""
    lvl = 1
    curr = total_xp
    
    # Chunk 1: 1-10 (100 XP each)
    if curr >= 1000:
        lvl += 10
        curr -= 1000
    else:
        l_gain = curr // 100
        return lvl + l_gain, curr % 100, 100

    # Chunk 2: 11-30 (200 XP each)
    if curr >= 4000:
        lvl += 20
        curr -= 4000
    else:
        l_gain = curr // 200
        return lvl + l_gain, curr % 200, 200

    # Chunk 3: 31-60 (350 XP each)
    if curr >= 10500:
        lvl += 30
        curr -= 10500
    else:
        l_gain = curr // 350
        return lvl + l_gain, curr % 350, 350

    # Chunk 4: 61+ (500 XP each)
    l_gain = curr // 500
    return lvl + l_gain, curr % 500, 500


async def get_cached_username(bot, user_id: int, *, allow_cache_write: bool = True) -> str:
    """
    Get user display name from cache or fetch from Discord.
    Prioritizes cache, local cache, bot cache, then API.
    Returns user ID as string if all fail.
    """
    # 1. Check bot's in-memory cache
    if user_id in bot.name_cache:
        return bot.name_cache[user_id]
    
    # 2. Try bot's get_user (Instant local cache check)
    user = bot.get_user(user_id)
    if user:
        if allow_cache_write:
            bot.name_cache[user_id] = user.display_name
        return user.display_name
    
    # 3. Try to fetch from Discord API
    try:
        user = await bot.fetch_user(user_id)
        if user:
            bot.name_cache[user_id] = user.display_name
            return user.display_name
    except:
        pass
    
    # 4. Fallback
    return str(user_id)

def is_user_banned(bot, user_id: int) -> bool:
    """Check if a user is banned."""
    return hasattr(bot, 'banned_users') and user_id in bot.banned_users

async def send_smart_message(ctx_or_interaction, message: str, ephemeral: bool = True, transient_duration: int = 30, user: discord.abc.User = None):
    """
    Sends a message intelligently:
    1. Tries interaction followup (ephemeral supported).
    2. If ephemeral is requested but no interaction, tries to DM the user.
    3. Falls back to channel send (transient via delete_after if ephemeral is desired).
    """
    # 1. Try Interaction
    target_user = user
    if not target_user:
        if hasattr(ctx_or_interaction, 'user'):
            target_user = ctx_or_interaction.user
        elif hasattr(ctx_or_interaction, 'author'):
            target_user = ctx_or_interaction.author
        elif hasattr(ctx_or_interaction, 'interaction') and ctx_or_interaction.interaction:
            target_user = ctx_or_interaction.interaction.user
        elif isinstance(ctx_or_interaction, (discord.User, discord.Member)):
            target_user = ctx_or_interaction

    if hasattr(ctx_or_interaction, 'interaction') and ctx_or_interaction.interaction:
        try:
            if not ctx_or_interaction.interaction.response.is_done():
                await ctx_or_interaction.interaction.response.send_message(content=message, ephemeral=ephemeral)
            else:
                await ctx_or_interaction.interaction.followup.send(content=message, ephemeral=ephemeral)
            return
        except:
            pass
    elif isinstance(ctx_or_interaction, discord.Interaction):
         target_user = target_user or ctx_or_interaction.user
         try:
            if not ctx_or_interaction.response.is_done():
                await ctx_or_interaction.response.send_message(content=message, ephemeral=ephemeral)
            else:
                await ctx_or_interaction.followup.send(content=message, ephemeral=ephemeral)
            return
         except:
            pass
            
    # 2. Try DM Fallback for Ephemeral
    if ephemeral and target_user:
        try:
            # Format nicely for DM
            dm_embed = discord.Embed(
                title="🔥 Streak Update",
                description=message,
                color=discord.Color.gold(),
                timestamp=discord.utils.utcnow()
            )
            dm_embed.set_footer(text="Wordle Game Bot • Personalized Notification")
            await target_user.send(embed=dm_embed)
            return
        except:
            # If DMs are closed, proceed to channel fallback
            pass

    # 3. Fallback to Channel
    try:
        # If strict ephemeral is requested but we have no interaction/DM, we use delete_after
        kwargs = {}
        if ephemeral and transient_duration:
            kwargs['delete_after'] = transient_duration
            
        target = ctx_or_interaction.channel if hasattr(ctx_or_interaction, 'channel') else ctx_or_interaction
        
        # If we failed DM, we send it in channel but maybe mention them if it's a transient message
        final_content = message
        if ephemeral and target_user:
            # If message already starts with mention, don't double it
            if not message.startswith('<@'):
                final_content = f"{target_user.mention} {message}"
            
        await target.send(content=final_content, **kwargs)
    except Exception as e:
        print(f"Failed to send smart message: {e}")
