import random
from discord.ext import commands
from src.utils import calculate_score, get_tier_display

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

def fetch_profile_stats_sync(bot: commands.Bot, user_id: int, guild_id: int):
    # 1. Fetch ALL data
    response = bot.supabase_client.table('scores').select('*').execute()
    all_data = response.data
    
    # --- A. SERVER STATS ---
    guild_data = [r for r in all_data if r['guild_id'] == guild_id]
    
    # User's stats in this guild
    s_row = next((r for r in guild_data if r['user_id'] == user_id), None)
    s_wins = s_row['wins'] if s_row else 0
    s_games = s_row['total_games'] if s_row else 0
    s_score = calculate_score(s_wins, s_games)
    
    # Calculate Server Tier & Rank
    server_scores = [calculate_score(r['wins'], r['total_games']) for r in guild_data]
    server_scores.sort() # Ascending order [10, 20, 30]
    
    # Percentile for Tier (How many people you beat)
    s_rank_idx = sum(1 for s in server_scores if s < s_score)
    s_perc = s_rank_idx / len(server_scores) if server_scores else 0
    s_tier_icon, s_tier_name = get_tier_display(s_perc)
    
    # Numerical Rank (1st, 2nd, etc) - Invert logic: Total - People you beat
    s_rank_num = len(server_scores) - s_rank_idx 

    # --- B. GLOBAL STATS ---
    global_map = {}
    for r in all_data:
        u = r['user_id']
        if u not in global_map: global_map[u] = {'w': 0, 'g': 0}
        global_map[u]['w'] += r['wins']
        global_map[u]['g'] += r['total_games']
        
    u_global = global_map.get(user_id, {'w': 0, 'g': 0})
    g_wins = u_global['w']
    g_games = u_global['g']
    g_score = calculate_score(g_wins, g_games)
    
    # Calculate Global Tier & Rank
    global_scores_list = [calculate_score(val['w'], val['g']) for val in global_map.values()]
    global_scores_list.sort()
    
    g_rank_idx = sum(1 for s in global_scores_list if s < g_score)
    g_perc = g_rank_idx / len(global_scores_list) if global_scores_list else 0
    g_tier_icon, g_tier_name = get_tier_display(g_perc)
    
    # Numerical Rank
    g_rank_num = len(global_scores_list) - g_rank_idx
    
    return (s_wins, s_games, s_score, s_tier_icon, s_tier_name, s_rank_num,
            g_wins, g_games, g_score, g_tier_icon, g_tier_name, g_rank_num)
