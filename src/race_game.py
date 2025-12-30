"""
Race game session management for Race Mode.
Tracks race lobby state, participants, and game coordination.
"""
import datetime
import discord
from typing import Dict, List, Set, Optional


class RaceSession:
    """Manages a race lobby and tracks participants before game starts."""
    
    __slots__ = ('channel_id', 'started_by', 'participants', 'secret', 'start_time', 
                 'lobby_message_id', 'status', 'race_games', 'completion_order', 'end_time', 
                 'duration_minutes', 'green_scores', 'final_results')
    
    def __init__(self, channel_id: int, started_by: discord.User, secret: str, lobby_message_id: int):
        self.channel_id = channel_id
        self.started_by = started_by
        self.participants: Dict[int, discord.User] = {started_by.id: started_by}  # user_id: User object
        self.secret = secret
        self.start_time = datetime.datetime.now()
        self.lobby_message_id = lobby_message_id
        self.status = 'waiting'  # waiting, active, finished
        self.race_games: Dict[int, 'WordleGame'] = {}  # user_id: game instance
        self.completion_order: List[tuple] = []  # [(user_id, won, time_taken), ...]
        self.duration_minutes = 10  # Default 10 minutes
        self.end_time = None
        self.green_scores: Dict[int, int] = {}  # user_id: total greens count
        self.final_results = [] # Stores final ranking info
    
    @property
    def participant_count(self) -> int:
        return len(self.participants)
    
    @property
    def can_start(self) -> bool:
        return self.participant_count >= 2
    
    def add_participant(self, user: discord.User) -> bool:
        """Add a participant to the race. Returns True if added, False if already joined."""
        if user.id in self.participants:
            return False
        self.participants[user.id] = user
        return True
    
    def is_participant(self, user_id: int) -> bool:
        """Check if user is a participant."""
        return user_id in self.participants
    
    def record_completion(self, user_id: int, won: bool, time_taken: float):
        """Record when a participant completes their race game."""
        # Update green score from game instance just to be sure
        game = self.race_games.get(user_id)
        if game:
             self.green_scores[user_id] = len(game.discovered_green_positions)
             
        self.completion_order.append((user_id, won, time_taken))
    
    @property
    def all_completed(self) -> bool:
        """Check if all participants have completed their games."""
        return len(self.completion_order) >= len(self.participants)
    
    @property
    def anyone_failed(self) -> bool:
        """Check if at least one participant failed to solve the word."""
        return any(not won for _, won, _ in self.completion_order)
    
    def get_rank(self, user_id: int) -> Optional[int]:
        """
        Get the rank of a participant (1-indexed). 
        Calculates rank dynamically based on current completion order.
        """
        # Sort current completions
        sorted_results = sorted(self.completion_order, key=lambda x: (x[1], -x[2]), reverse=True) # Won desc, Time asc
        
        for idx, (uid, _, _) in enumerate(sorted_results, 1):
             if uid == user_id:
                 return idx
        return None

    def conclude_race(self, bot):
        """
        Finalize the race, calculate rewards, and generate the results embed.
        Sort Logic:
        1. Won = True (Primary) -> Time Taken ASC (Secondary)
        2. Won = False -> Green Count DESC (Secondary) -> Time Taken ASC (Tertiary)
        """
        results = []
        
        # 1. Gather all data
        for user_id, user in self.participants.items():
            game = self.race_games.get(user_id)
            if not game: continue
            
            # Check if user is in completion_order
            completed_info = next((x for x in self.completion_order if x[0] == user_id), None)
            
            if completed_info:
                won, time_taken = completed_info[1], completed_info[2]
            else:
                # User timed out or game ended abruptly
                won = False
                time_taken = (datetime.datetime.now() - game.start_time).total_seconds()
            
            green_count = len(game.discovered_green_positions)
            
            results.append({
                'user_id': user_id,
                'user': user,
                'won': won,
                'time_taken': time_taken,
                'green_count': green_count,
                'attempts': game.attempts_used,
                'max_attempts': game.max_attempts,
                'game': game
            })
            
        # 2. Sort Logic
        # Sort keys: Won (True>False), Green Count (High>Low), Time (Low>High)
        # Python sort is stable, so we sort by least important first or use complex key
        
        results.sort(key=lambda x: (
            x['won'],                       # True(1) > False(0)
            x['green_count'] if not x['won'] else 0,  # Only for non-winners matter
            -x['time_taken']                # Negative for ASC sort in reverse=True context?? No wait.
        ), reverse=True)
        
        # Let's simple key properties for clarity:
        # We want: 
        #   Winners (Fastest First)
        #   Losers (Most Greens First -> Fastest First)
        
        def rank_key(item):
            # Returns a tuple that compares correctly with default sort (ASC) or reverse?
            # Let's return a "Score" where higher is better.
            
            if item['won']:
                # Score = 100000 - time_taken (Fastest time = Higher Score)
                return 100000 - item['time_taken']
            else:
                # Score = (Green Count * 1000) + (1000 - Time Taken)
                # Max time ~ 10 mins = 600s
                return (item['green_count'] * 1000) + (1000 - min(item['time_taken'], 1000))
        
        results.sort(key=rank_key, reverse=True)
        
        # 3. Calculate Rewards & Store
        from src.mechanics.rewards import calculate_race_rewards_delayed
        
        final_summary = []
        for idx, res in enumerate(results, 1):
             rewards = calculate_race_rewards_delayed(bot, res['user_id'], res['game'], idx)
             res['rewards'] = rewards
             res['rank'] = idx
             final_summary.append(res)
             
        self.final_results = final_summary
        return final_summary

