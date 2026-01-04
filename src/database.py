import random
from discord.ext import commands

from src.config import TIERS, XP_GAINS, XP_LEVELS, MPS_BASE, MPS_EFFICIENCY, MPS_SPEED

# --- V2 SCORING & PROGRESSION ---

from src.mechanics.rewards import calculate_final_rewards
import datetime
import time

# --- PROFILE CACHE ---
# Simple TTL Cache to reduce DB pressure on frequent lookups
_PROFILE_CACHE = {} # {user_id: (data, expiry)}
CACHE_TTL = 300 # 5 minutes

def get_daily_wr_gain(bot: commands.Bot, user_id: int) -> int:
    """
    Calculates total WR gained by the user today (UTC).
    Queries 'match_history' table. Returns 0 if table not found or error.
    """
    try:
        today_start = datetime.datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Assumption: Table is 'match_history' and has 'user_id', 'created_at', 'wr_delta'
        # We only care about POSITIVE gains for the cap.
        response = bot.supabase_client.table('match_history') \
            .select('wr_delta') \
            .eq('user_id', user_id) \
            .gte('created_at', today_start.isoformat()) \
            .gt('wr_delta', 0) \
            .execute()
            
        if response.data:
            total = sum(r['wr_delta'] for r in response.data)
            return total
        return 0
        return 0
    except Exception as e:
        # print(f"⚠️ Could not fetch daily WR stats: {e}")
        return 0

def get_daily_wins(bot: commands.Bot, user_id: int) -> int:
    """Calculates number of wins by the user today (UTC)."""
    try:
        today_start = datetime.datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        response = bot.supabase_client.table('match_history') \
            .select('count', count='exact') \
            .eq('user_id', user_id) \
            .eq('won', True) \
            .gte('created_at', today_start.isoformat()) \
            .execute()
        
        if response.count is not None:
            return response.count
        return 0
    except Exception as e:
        return 0

def record_game_v2(bot: commands.Bot, user_id: int, guild_id: int, mode: str, 
                   outcome: str, guesses: int, time_taken: float, 
                   egg_trigger: str = None, pre_wr: int = None, pre_daily: int = None):
    """
    Calls the DB RPC to record game results.
    Allows passing pre_wr and pre_daily to skip redundant fetches (Performance).
    """
    try:
        # 1. Fetch current stats for Tier calc (if not pre-fetched)
        current_wr = pre_wr
        if current_wr is None:
            try:
                # We need current WR to determine Tier Penalty
                u_res = bot.supabase_client.table('user_stats_v2').select('multi_wr').eq('user_id', user_id).execute()
                if u_res.data:
                    current_wr = u_res.data[0]['multi_wr']
                else:
                    current_wr = 0
            except:
                current_wr = 0
            
        # 2. Fetch daily progress for Anti-Grind (if not pre-fetched)
        daily_gain = pre_daily if pre_daily is not None else get_daily_wr_gain(bot, user_id)
        
        # 3. Calculate Final Rewards
        xp_gain, wr_delta = calculate_final_rewards(mode, outcome, guesses, time_taken, current_wr, daily_gain)
        
        # --- STREAK INTEGRATION ---
        streak_msg = None
        badge_awarded = None
        
        if mode != 'CUSTOM':
            from src.mechanics.streaks import StreakManager
            streak_mgr = StreakManager(bot)
            
            # Check/Update Streak (Increments if new day, regardless of win/loss)
            streak_msg, raw_mult, badge_awarded = streak_mgr.check_streak(user_id)
            
            # Apply Multiplier if WIN and within limit
            if outcome == 'win' and raw_mult > 1.0:
                daily_wins = get_daily_wins(bot, user_id)
                limit = 0
                if raw_mult >= 3.0: limit = 4
                elif raw_mult >= 2.5: limit = 4
                elif raw_mult >= 2.0: limit = 3
                
                if daily_wins < limit:
                    wr_delta = int(wr_delta * raw_mult)
        # --------------------------
        
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
        
        # Invalidate Cache
        if user_id in _PROFILE_CACHE:
            del _PROFILE_CACHE[user_id]
        
        if response.data:
            from src.utils import calculate_level
            
            data = response.data
            new_xp = data.get('xp', 0)
            
            # Identify Old XP
            old_xp = new_xp - xp_gain
            if old_xp < 0: old_xp = 0 # Safety
            
            old_wr = 0
            new_wr = 0
            if mode == 'SOLO':
                 new_wr = data.get('solo_wr', 0)
            else:
                 new_wr = data.get('multi_wr', 0)
                 
            old_wr = new_wr - wr_delta
            
            # Check Tier Cross
            # Find old and new tier
            from src.config import TIERS
            
            old_tier = None
            new_tier = None
            
            # Check Tier Cross - Ensure we find the HIGHEST tier first
            # We assume TIERS is High -> Low WR
            for t in TIERS:
                if old_tier is None and old_wr >= t['min_wr']: 
                    old_tier = t
                if new_tier is None and new_wr >= t['min_wr']: 
                    new_tier = t
                if old_tier and new_tier: break
                
            # If crossed threshold UP
            if new_tier and old_tier:
                if new_tier['min_wr'] > old_tier['min_wr']:
                    data['tier_up'] = new_tier
            elif new_tier and not old_tier: # Transition from unranked (-ve or 0)
                 data['tier_up'] = new_tier
            
            old_lvl = calculate_level(old_xp)
            new_lvl = calculate_level(new_xp)
            
            data['xp_gain'] = xp_gain
            data['wr_delta_raw'] = wr_delta
            
            if new_lvl > old_lvl:
                data['level_up'] = new_lvl
                
            return {
                'xp': new_xp, 'solo_wr': data.get('solo_wr',0), 'multi_wr': data.get('multi_wr',0),
                'xp_gain': xp_gain, 'level_up': data.get('level_up'), 'tier_up': data.get('tier_up'),
                'streak_msg': streak_msg, 'streak_badge': badge_awarded
            }
        return None
        
    except Exception as e:
        print(f"DB ERROR in record_game_v2: {e}")
        return None

def fetch_user_profile_v2(bot: commands.Bot, user_id: int, use_cache: bool = True):
    """Fetches full profile V2 with optional caching."""
    now = time.monotonic()
    
    if use_cache and user_id in _PROFILE_CACHE:
        data, expiry = _PROFILE_CACHE[user_id]
        if now < expiry:
            return data
            
    try:
        response = bot.supabase_client.table('user_stats_v2').select('*').eq('user_id', user_id).execute()
        if response.data:
            data = response.data[0]
            from src.utils import get_level_progress
            lvl, cur_xp, needed = get_level_progress(data['xp'])
            data['level'] = lvl
            data['current_level_xp'] = cur_xp
            data['next_level_xp'] = needed
            
            wr = data['multi_wr']
            tier_info = TIERS[-1] 
            for t in TIERS:
                if wr >= t['min_wr']:
                    tier_info = t
                    break
            data['tier'] = tier_info
            
            # --- FETCH STREAK INFO ---
            try:
                s_res = bot.supabase_client.table('streaks_v4').select('current_streak, max_streak').eq('user_id', user_id).execute()
                if s_res.data:
                    data['current_streak'] = s_res.data[0].get('current_streak', 0)
                    data['max_streak'] = s_res.data[0].get('max_streak', 0)
                else:
                    data['current_streak'] = 0
                    data['max_streak'] = 0
            except:
                data['current_streak'] = 0
                data['max_streak'] = 0
            
            # Update Cache
            _PROFILE_CACHE[user_id] = (data, now + CACHE_TTL)
            return data
        return None
    except Exception as e:
        print(f"DB ERROR in fetch_user_profile_v2: {e}")
        return None

def fetch_user_profiles_batched(bot: commands.Bot, user_ids: list):
    """
    Industry-grade optimization: Fetch multiple profiles in ONE API call.
    Used for race conclusions to avoid N database queries.
    """
    if not user_ids: return {}
    
    try:
        # Use .in_ filters for batching
        response = bot.supabase_client.table('user_stats_v2').select('*').in_('user_id', user_ids).execute()
        
        results = {}
        from src.utils import get_level_progress
        now = time.monotonic()
        
        for data in response.data:
            uid = data['user_id']
            lvl, cur_xp, needed = get_level_progress(data['xp'])
            data['level'] = lvl
            data['current_level_xp'] = cur_xp
            data['next_level_xp'] = needed
            
            wr = data['multi_wr']
            tier_info = TIERS[-1]
            for t in TIERS:
                if wr >= t['min_wr']:
                    tier_info = t
                    break
            data['tier'] = tier_info
            
            results[uid] = data
            _PROFILE_CACHE[uid] = (data, now + CACHE_TTL) # Back-fill cache
            
        return results
    except Exception as e:
        print(f"DB ERROR in fetch_user_profiles_batched: {e}")
        return {}

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

# --- BITSET WORD POOL OPTIMIZATION ---

def get_next_word_bitset(bot: commands.Bot, guild_id: int, pool_type: str = 'simple') -> str:
    """
    Industry-grade optimization: Gets a secret word using a Guild-level bitset.
    Avoids O(N) database growth and provides atomic selection.
    """
    try:
        # 1. Determine which pool and total words
        if pool_type == 'classic':
            pool_list = bot.hard_secrets
        else:
            pool_list = bot.secrets
            
        total_words = len(pool_list)
        if total_words == 0:
            return random.choice(['ABOUT', 'PANIC', 'PIZZA', 'LIGHT', 'DREAM']) # Absolute fallback

        # 2. Call RPC to get a random unused index
        params = {
            'p_guild_id': guild_id,
            'p_pool_type': pool_type,
            'p_total_words': total_words
        }
        
        response = bot.supabase_client.rpc('pick_next_word', params).execute()
        
        if response.data is not None:
            idx = int(response.data)
            # 3. Return word at that index
            return pool_list[idx]
            
        return random.choice(pool_list)
        
    except Exception as e:
        print(f"DB ERROR in get_next_word_bitset: {e}")
        # Final fallback
        pool = bot.hard_secrets if pool_type == 'classic' else bot.secrets
        return random.choice(pool) if pool else "PANIC"

def record_race_result(bot: commands.Bot, user_id: int, word: str, won: bool, guesses: int, time_taken: float, xp: int, wr: int, rank: int):
    """Record race mode result in database."""
    try:
        # Record in match history (optional - for tracking purposes)
        bot.supabase_client.table('match_history').insert({
            'user_id': user_id,
            'mode': 'RACE',
            'word': word,
            'won': won,
            'guesses': guesses,
            'time_taken': time_taken,
            'xp_delta': xp,
            'wr_delta': wr,
            'rank': rank,
            'created_at': datetime.datetime.utcnow().isoformat()
        }).execute()
        
        # Update user stats
        bot.supabase_client.rpc('record_game_result_v4', {
            'p_user_id': user_id,
            'p_guild_id': None,  # Race is cross-server
            'p_mode': 'MULTI',  # Treat as multiplayer for WR purposes
            'p_xp_gain': xp,
            'p_wr_delta': wr,
            'p_is_win': won,
            'p_egg_trigger': None
        }).execute()
        
        return True
    except Exception as e:
        print(f"DB ERROR in record_race_result: {e}")
        return False
