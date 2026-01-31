from src.mechanics.rewards import calculate_final_rewards
from src.config import TIERS

def test_rewards():
    print("--- Testing Granular WR Rewards ---")
    
    # High-tier player (Grandmaster)
    gm_wr = 2800
    # Before, this would forced -15 on loss.
    # Now, let's see what happens.
    
    # Case 1: Win, 3 tries, 15 seconds
    xp, wr = calculate_final_rewards('MULTI', 'win', 3, 15, gm_wr, 0)
    print(f"GM Win (3 tries, 15s): XP={xp}, Score={wr}")
    
    # Case 2: Participation (Loss), 6 tries, 120 seconds
    xp, wr = calculate_final_rewards('MULTI', 'participation', 6, 120, gm_wr, 0)
    print(f"GM Participation (Loss): XP={xp}, Score={wr}")
    
    # Case 3: Solo Win, 4 tries, 45 seconds
    xp, wr = calculate_final_rewards('SOLO', 'win', 4, 45, 1000, 0)
    print(f"Solo Win (Elite): XP={xp}, Score={wr}")

    # Case 4: Solo Loss
    xp, wr = calculate_final_rewards('SOLO', 'loss', 6, 999, 1000, 0)
    print(f"Solo Loss (Elite): XP={xp}, Score={wr}")

if __name__ == "__main__":
    test_rewards()
