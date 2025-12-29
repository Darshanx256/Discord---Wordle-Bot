from src.config import XP_GAINS, MPS_BASE, MPS_EFFICIENCY, MPS_SPEED, TIERS, DAILY_CAP_1, DAILY_CAP_2

# Pre-sort TIERS by min_wr descending for efficient tier lookups
_SORTED_TIERS = sorted(TIERS, key=lambda x: x['min_wr'], reverse=True)

def calculate_base_rewards(mode: str, outcome: str, guesses: int, time_taken: float):
    """
    Calculates base XP and WR (MPS) before any penalties.
    Refactored from src.database.calculate_game_rewards.
    """
    xp = 0
    mps = 0
    
    # 1. Base Rewards
    if mode == 'SOLO':
        rewards = XP_GAINS['SOLO']
        xp = rewards.get(outcome, 5)
        
        if outcome == 'win':
            # Solo Win Base: 100 (Assumed standard)
            mps = 100 
            mps += MPS_EFFICIENCY.get(guesses, 0)
            if time_taken < 60: mps += MPS_SPEED[60]
            elif time_taken < 90: mps += MPS_SPEED[90]
        else:
            mps = -15 

    else: # MULTIPLAYER
        xp = XP_GAINS['MULTI'].get(outcome, 5)
        mps = MPS_BASE.get(outcome, 0)
        
        if outcome == 'win':
            mps += MPS_EFFICIENCY.get(guesses, 0)
            if time_taken < 60: 
                mps += MPS_SPEED[60]
                xp += XP_GAINS['BONUS']['under_60s']
            elif time_taken < 90: 
                mps += MPS_SPEED[90]
                xp += XP_GAINS['BONUS']['under_90s']
                
    return xp, mps

def get_tier_multiplier(current_wr: int) -> float:
    """Returns the reward multiplier based on current tier."""
    for tier in _SORTED_TIERS:
        if current_wr >= tier['min_wr']:
            return tier.get('multiplier', 1.0)
            
    return 1.0

def apply_anti_grind(xp: int, wr: int, daily_wr_gain: int) -> tuple[int, int]:
    """
    Applies Anti-Grind penalties based on accumulated daily WR gain.
    Caps apply to the NEXT game.
    """
    multiplier = 1.0
    
    # Safety: ensure non-negative daily gain
    safe_daily = max(0, daily_wr_gain)
    
    if safe_daily >= DAILY_CAP_2:
        multiplier = 0.25 # Drop 50% then another 50% = 25% remaining
    elif safe_daily >= DAILY_CAP_1:
        multiplier = 0.50 # Drop 50%
        
    final_xp = int(xp * multiplier)
    final_wr = int(wr * multiplier)
    
    return final_xp, final_wr

def calculate_final_rewards(mode: str, outcome: str, guesses: int, time_taken: float, current_wr: int, daily_wr_gain: int):
    """
    Orchestrates the full reward calculation:
    1. Base Calculation
    2. Tier Penalty
    3. Anti-Grind Penalty
    """
    # 1. Base
    xp, wr = calculate_base_rewards(mode, outcome, guesses, time_taken)
    
    # Tier-based deduction: Apply multiplier to all rewards (no more fixed -15 penalty)
    # All outcomes use tier multiplier for both XP and WR
    
    # Apply modifiers
    t_mult = get_tier_multiplier(current_wr)
    
    # XP is always reduced (Reward for playing)
    xp = int(xp * t_mult)
    
    # WR is reduced only if it's a GAIN. Penalties should remain full.
    if wr > 0:
        wr = int(wr * t_mult)
        
    # Anti-Grind
    # Calculates potential reduced values
    ag_xp, ag_wr = apply_anti_grind(xp, wr, daily_wr_gain)
    
    # XP always reduced by Anti-Grind check
    xp = ag_xp
    
    # WR reduced only if it was positive
    if wr > 0:
        wr = ag_wr
        
    return xp, wr

def calculate_race_rewards(bot, user_id: int, game, rank: int):
    """
    Calculate rewards for Race Mode.
    - 1st place: 110% of normal rewards (10% bonus)
    - Others: 45% of normal rewards
    """
    from src.database import fetch_user_profile_v2
    
    # Get user profile for tier calculation
    profile = fetch_user_profile_v2(bot, user_id)
    current_wr = profile.get('wr_multi', 0) if profile else 0
    daily_wr_gain = profile.get('daily_wr_gain', 0) if profile else 0
    
    # Calculate base rewards
    time_taken = (game.last_interaction - game.start_time).total_seconds()
    xp, wr = calculate_final_rewards('MULTI', 'win', game.attempts_used, time_taken, current_wr, daily_wr_gain)
    
    # Apply rank multiplier
    if rank == 1:
        # 1st place gets 110% (10% bonus)
        xp = int(xp * 1.10)
        wr = int(wr * 1.10)
        rank_msg = "**1st Place Bonus!** +10%"
    else:
        # Others get 45%
        xp = int(xp * 0.45)
        wr = int(wr * 0.45)
        rank_msg = f"**Rank #{rank}** rewards"
    
    # Record in database
    from src.database import record_race_result
    record_race_result(bot, user_id, game.secret, True, game.attempts_used, time_taken, xp, wr, rank)
    
    # Build message
    message = f"{rank_msg}\n+{xp} XP | +{wr} WR"
    
    return {
        'xp': xp,
        'wr': wr,
        'rank': rank,
        'message': message
    }
