from src.config import XP_GAINS, MPS_BASE, MPS_EFFICIENCY, MPS_SPEED, TIERS, DAILY_CAP_1, DAILY_CAP_2

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
            if time_taken < 30: mps += MPS_SPEED[30]
            elif time_taken < 40: mps += MPS_SPEED[40]
        else:
            mps = -15 

    else: # MULTIPLAYER
        xp = XP_GAINS['MULTI'].get(outcome, 5)
        mps = MPS_BASE.get(outcome, 0)
        
        if outcome == 'win':
            mps += MPS_EFFICIENCY.get(guesses, 0)
            if time_taken < 30: 
                mps += MPS_SPEED[30]
                xp += XP_GAINS['BONUS']['under_30s']
            elif time_taken < 40: 
                mps += MPS_SPEED[40]
                xp += XP_GAINS['BONUS']['under_40s']
                
    return xp, mps

def get_tier_multiplier(current_wr: int) -> float:
    """Returns the reward multiplier based on current tier."""
    # Find highest matching tier
    # TIERS are ordered desc, but config shows desc order? 
    # Config: GM(2800), Master(1600), Elite(900), Challenger(0)
    # We should iterate and find the first one that matches min_wr
    
    for tier in TIERS:
        if current_wr >= tier['min_wr']:
            return tier.get('multiplier', 1.0)
            
    # If below Challenger (unranked? or just fallback)
    # The config says Challenger 0+, so everyone is at least Challenger if ranked?
    # Assume 1.0 if not found (Unranked?)
    return 1.0

def apply_anti_grind(xp: int, wr: int, daily_wr_gain: int) -> tuple[int, int]:
    """
    Applies Anti-Grind penalties based on accumulated daily WR gain.
    Caps apply to the NEXT game.
    """
    multiplier = 1.0
    
    if daily_wr_gain >= DAILY_CAP_2:
        multiplier = 0.25 # Drop 50% then another 50% = 25% remaining
    elif daily_wr_gain >= DAILY_CAP_1:
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
    
    # Check if High Tier (>= 2800) for negative rating penalty on non-wins
    is_high_tier = (current_wr >= 2800)
    
    if is_high_tier and outcome not in ['win', 'correct_4', 'correct_3', 'correct_2', 'correct_1']:
         # This covers 'loss' (Solo) and 'participation' (Multi)
         # Force negative WR penalty
         wr = -15
         
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
