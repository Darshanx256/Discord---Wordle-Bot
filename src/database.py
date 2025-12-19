import random
from discord.ext import commands
from src.utils import calculate_score, get_tier_display # Keep for backward compat if needed, but we likely won't use them for V2
from src.config import TIERS, XP_GAINS, XP_LEVELS, MPS_BASE, MPS_EFFICIENCY, MPS_SPEED

# --- V2 SCORING & PROGRESSION ---

from src.mechanics.rewards import calculate_final_rewards
import datetime

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
    except Exception as e:
        # print(f"⚠️ Could not fetch daily WR stats: {e}")
        return 0

def record_game_v2(bot: commands.Bot, user_id: int, guild_id: int, mode: str, 
                   outcome: str, guesses: int, time_taken: float, egg_trigger: str = None):
    """
    Calls the DB RPC to record game results.
    """
    try:
        # 1. Fetch current stats for Tier calc
        current_wr = 0
        try:
            # We need current WR to determine Tier Penalty
            # Optimize: Maybe pass it in? For now fetch.
            u_res = bot.supabase_client.table('user_stats_v2').select('multi_wr').eq('user_id', user_id).execute()
            if u_res.data:
                current_wr = u_res.data[0]['multi_wr']
        except:
            pass
            
        # 2. Fetch daily progress for Anti-Grind
        daily_gain = get_daily_wr_gain(bot, user_id)
        
        # 3. Calculate Final Rewards
        xp_gain, wr_delta = calculate_final_rewards(mode, outcome, guesses, time_taken, current_wr, daily_gain)
        
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
                
            return data # {xp, solo_wr, multi_wr, games_today, xp_gain, level_up?, tier_up?}
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
            # Use centralized utility for consistent math
            from src.utils import get_level_progress
            
            lvl, cur_xp, needed = get_level_progress(data['xp'])
            
            data['level'] = lvl
            data['current_level_xp'] = cur_xp
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
