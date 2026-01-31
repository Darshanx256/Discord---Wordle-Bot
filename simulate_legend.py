from src.mechanics.rewards import calculate_final_rewards
from src.config import TIERS

def run_legend_sim():
    legend_wr = 4000
    daily_gain = 0
    
    outcomes = ['win', 'correct_4', 'correct_3', 'correct_2', 'correct_1']
    
    print("=== Legend Tier Reward Simulation (0.4x Multiplier) ===")
    print(f"{'Outcome':<15} | {'XP':<5} | {'WR Gain':<8}")
    print("-" * 35)
    
    for outcome in outcomes:
        # Standard Multiplayer simulation: 3 guesses, 45 seconds (no time bonus)
        xp, wr = calculate_final_rewards('MULTI', outcome, guesses=3, time_taken=45, current_wr=legend_wr, daily_wr_gain=daily_gain)
        print(f"{outcome:<15} | {xp:<5} | {wr:<8}")

if __name__ == "__main__":
    run_legend_sim()
