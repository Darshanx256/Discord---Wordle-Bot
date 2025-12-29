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
                 'lobby_message_id', 'status', 'race_games', 'completion_order')
    
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
        """Get the rank of a participant (1-indexed). Returns None if not completed yet."""
        for idx, (uid, _, _) in enumerate(self.completion_order, 1):
            if uid == user_id:
                return idx
        return None
