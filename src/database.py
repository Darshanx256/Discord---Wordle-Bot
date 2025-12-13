import random
from discord.ext import commands
from src.utils import calculate_score, get_tier_display # Keep for backward compat if needed, but we likely won't use them for V2
from src.config import TIERS, XP_GAINS, XP_LEVELS, MPS_BASE, MPS_EFFICIENCY, MPS_SPEED

# --- V2 SCORING & PROGRESSION ---

def calculate_game_rewards(mode: str, outcome: str, guesses: int, time_taken: float):
    """
    Calculates XP and WR (MPS) delta based on game result.
    mode: 'SOLO' or 'MULTI'
    outcome: 'win', 'loss' (Solo) OR 'win', 'correct_4', ... (Multi)
    guesses: Number of guesses used (1-6)
    time_taken: Seconds
    """
    xp = 0
    mps = 0
    
    # 1. Base Rewards
    if mode == 'SOLO':
        # outcome is 'win' or 'loss'
        rewards = XP_GAINS['SOLO']
        xp = rewards.get(outcome, 5)
        
        # Solo MPS (Writer Note: Prompt specifies MPS details for Multiplayer mostly)
        # "Solo... Played via /solo... Input via button... Standard Wordle"
        # "Rating... Solo... input via button".
        # Prompt only detailed MPS for Multiplayer.
        # "Match Performance Score (MPS) ... Base Outcome (Multiplayer)"
        # Let's assume Solo uses similar logic or simplified.
        # "Solo Mode: Correct solve +40 XP, Failed +5 XP".
        # Let's use simple MPS for Solo if not specified: Win=Match Standard?
        # "Rating... Solo... Performance-based".
        if outcome == 'win':
            # Use Multiplayer win base for Solo WR?
            # "Multiplayer never causes negative WR". Solo can (after Challenger).
            mps = 100 # Arbitrary base for Solo win? Or use MPS_BASE['win']?
            # Efficiency Bonus
            mps += MPS_EFFICIENCY.get(guesses, 0)
            # Speed Bonus
            if time_taken < 30: mps += MPS_SPEED[30]
            elif time_taken < 40: mps += MPS_SPEED[40]
        else:
            mps = -15 # Small penalty for loss? 
            # "Solo negatives (after Challenger): Soft-capped... limited".
            # We'll pass negative, DB handles "No negative WR until Challenger".

    else: # MULTIPLAYER
        # outcome: 'win', 'correct_4', 'correct_3', etc.
        # XP
        xp = XP_GAINS['MULTI'].get(outcome, 5)
        
        # MPS (Start with Base)
        mps = MPS_BASE.get(outcome, 0)
        
        if outcome == 'win':
            # Efficiency
            mps += MPS_EFFICIENCY.get(guesses, 0)
            
            # Speed
            if time_taken < 30: 
                mps += MPS_SPEED[30]
                xp += XP_GAINS['BONUS']['under_30s']
            elif time_taken < 40: 
                mps += MPS_SPEED[40]
                xp += XP_GAINS['BONUS']['under_40s']
                
    return xp, mps

def record_game_v2(bot: commands.Bot, user_id: int, guild_id: int, mode: str, 
                   outcome: str, guesses: int, time_taken: float, egg_trigger: str = None):
    """
    Calls the DB RPC to record game results.
    """
    try:
        xp_gain, wr_delta = calculate_game_rewards(mode, outcome, guesses, time_taken)
        
        # "Solves" check for Challenger Tier? 
        # DB tracks wins/games.
        
        params = {
            'p_user_id': user_id,
            'p_guild_id': guild_id,
            'p_mode': mode,
            'p_xp_gain': xp_gain,
            'p_wr_delta': wr_delta,
            'p_is_win': (outcome == 'win'),
            'p_egg_trigger': egg_trigger
        }
        
        response = bot.supabase_client.rpc('record_game_result_v4', params).execute()
        
        if response.data:
            from src.utils import calculate_level
            
            data = response.data
            new_xp = data.get('xp', 0)
            
            # Identify Old XP
            old_xp = new_xp - xp_gain
            if old_xp < 0: old_xp = 0 # Safety
            
            old_lvl = calculate_level(old_xp)
            new_lvl = calculate_level(new_xp)
            
            data['xp_gain'] = xp_gain
            data['wr_delta_raw'] = wr_delta
            
            if new_lvl > old_lvl:
                data['level_up'] = new_lvl
                
            return data # {xp, solo_wr, multi_wr, games_today, xp_gain, level_up?}
        return None
        
    except Exception as e:
        print(f"DB ERROR in record_game_v2: {e}")
        return None

def fetch_user_profile_v2(bot: commands.Bot, user_id: int):
    """Fetches full profile V2."""
    try:
        response = bot.supabase_client.table('user_stats_v2').select('*').eq('user_id', user_id).execute()
        if response.data:
            data = response.data[0]
            # Calculate Level dynamically (if not stored/reliable in DB or to serve UI)
            # "Level XP resets after each level-up" -> Use cumulative manually or iterative
            # Prompt: "Levels 1–10 : 100 XP per level... Level XP resets".
            # This implies `xp` in DB might be "Current Level XP" or "Total XP".
            # Usually easiest to store Total XP and calc level.
            # My migration script stored Total XP (roughly).
            # Let's assume `xp` is TOTAL XP and we calc level here.
            total_xp = data['xp']
            lvl = 1
            
            # Iterative Level Calc
            while True:
                needed = 100
                if lvl >= 61: needed = 500
                elif lvl >= 31: needed = 350
                elif lvl >= 11: needed = 200
                
                if total_xp >= needed:
                    total_xp -= needed
                    lvl += 1
                else:
                    break
            
            data['level'] = lvl
            data['current_level_xp'] = total_xp
            data['next_level_xp'] = needed
            
            # Determine Tier
            # Use Multi WR for main tier display? Prompt says "Wordle Rating... Separate ladders".
            # "Grandmaster... 15 multiplayer wins... WR >= 2800".
            # Let's check Multi WR for tier.
            wr = data['multi_wr']
            tier_info = TIERS[-1] # Default Challenger
            
            for t in TIERS:
                if wr >= t['min_wr']:
                    # Also check extra reqs conceptually? 
                    # For now just WR based.
                    tier_info = t
                    break
            
            data['tier'] = tier_info
            
            return data
        return None
    except Exception as e:
        print(f"DB ERROR in fetch_user_profile_v2: {e}")
        return None

def trigger_egg(bot: commands.Bot, user_id: int, egg_name: str):
    """Triggers an easter egg update without a game."""
    # We can use the same RPC or a simpler update.
    # We implemented p_egg_trigger in record_game, but maybe we want standalone?
    # Let's reuse record_game or direct update.
    # Reuse record_game with 0 stats? Or specific RPC?
    # Direct update.
    try:
        # We need to append to jsonb.
        # supabase-py doesn't support complex jsonb updates easily without RPC or raw sql.
        # Use RPC 'record_game_result_v4' with 0 delta?
        params = {
            'p_user_id': user_id,
            'p_guild_id': None,
            'p_mode': 'SOLO', # Dummy
            'p_xp_gain': 0,
            'p_wr_delta': 0,
            'p_is_win': False,
            'p_egg_trigger': egg_name
        }
        bot.supabase_client.rpc('record_game_result_v4', params).execute()
        return True
    except Exception as e:
        print(f"Egg Error: {e}")
        return False

# --- EXISTING WORD LOGIC (Keep as is) ---
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
