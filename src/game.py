import datetime
import discord
from src.utils import EMOJIS

# ========= 4. GAME CLASS =========
class WordleGame:
    __slots__ = ('secret', 'secret_set', 'channel_id', 'started_by', 'max_attempts', 'history', 
                 'used_letters', 'participants', 'guessed_words', 'last_interaction', 'message_id', 'start_time',
                 'reveal_on_loss', 'difficulty', 'custom_dict', 'time_limit', 'allowed_players', 'show_keyboard',
                 'blind_mode', 'custom_only', 'discovered_green_positions', 'title', 'monotonic_end_time',
                 'hard_mode', 'hard_constraints')

    def __init__(self, secret: str, channel_id: int, started_by: discord.abc.User, message_id: int):
        self.secret = secret
        self.secret_set = set(secret) # Store secret as a set for O(1) lookups
        self.channel_id = channel_id
        self.started_by = started_by
        self.max_attempts = 6
        self.history = [] 
        self.participants = set() 
        self.guessed_words = set()
        self.monotonic_end_time = None
        self.used_letters = {'correct': set(), 'present': set(), 'absent': set()}
        self.last_interaction = datetime.datetime.now()
        self.start_time = datetime.datetime.now()
        self.message_id = message_id
        self.reveal_on_loss = True  # Default for custom games
        self.difficulty = 0         # 0=Simple, 1=Classic, 2=Custom
        # Enhanced custom game fields
        self.custom_dict = None      # Set of allowed words for custom games
        self.time_limit = None       # Time limit in minutes
        self.allowed_players = set()  # Set of user IDs allowed to guess
        self.show_keyboard = True    # Whether to show keyboard guide
        self.blind_mode = False      # False, 'full', or 'green'
        self.custom_only = False     # If True, only custom dictionary words are allowed
        self.discovered_green_positions = set()  # Track which positions have greens discovered
        self.title = None            # Custom title for the game embed
        
        # Hard Mode
        self.hard_mode = False
        self.hard_constraints = {'greens': [None]*5, 'present': set()}

    @property
    def attempts_used(self): return len(self.history)

    def is_duplicate(self, word: str) -> bool: return word.upper() in self.guessed_words

    def evaluate_guess(self, guess: str) -> str: 
        # Using global EMOJIS from src.utils handled via import if strictly needed inside or pass it.
        # But wait, original code passed EMOJIS as arg in line 405: evaluate_guess(self, guess, EMOJIS)
        # But I can just use the imported EMOJIS directly.
        
        guess = guess.upper()
        secret_upper = self.secret.upper() 
        s_list = list(secret_upper)
        g_list = list(guess)
        state_list = ["white"] * 5
        
        # 1. Greens (Exact Matches)
        for i in range(5):
            if g_list[i] == s_list[i]:
                state_list[i] = "green"
                # Mark both letter slots as used
                s_list[i] = None
                g_list[i] = None
                
                # Update keyboard state
                letter = guess[i].lower()
                self.used_letters['correct'].add(letter)
                self.used_letters['present'].discard(letter)
                
                # Track discovered green positions for race scoring
                self.discovered_green_positions.add(i)

        # 2. Yellows (Misplaced)
        for i in range(5):
            if state_list[i] == "green": continue
            ch = g_list[i]
            
            if ch is not None and ch in s_list:
                state_list[i] = "yellow"
                
                s_list[s_list.index(ch)] = None 
                g_list[i] = None # <--- Important!
                letter = ch.lower()
                if letter not in self.used_letters['correct']:  
                    self.used_letters['present'].add(letter)
                    
        # 3. Absents (Grey) & Keyboard State Update
        for i in range(5):
             ch = guess[i]
             
             letter = ch.lower()
             
             if state_list[i] == "white":
                 self.used_letters['absent'].add(letter)

        # Ensuring that letters marked 'correct' or 'present' are not in 'absent' 
        self.used_letters['absent'] -= (self.used_letters['correct'] | self.used_letters['present'])

        # --- Phase 4: Construct the final emoji pattern string ---
        emoji_tags = [""] * 5
        for i in range(5):
            char = guess[i].lower()
            state = state_list[i]
            emoji_key = f"block_{char}_{state}"
            emoji_tags[i] = EMOJIS.get(emoji_key, char.upper())

        return "".join(emoji_tags)

    def validate_hard_mode_guess(self, guess: str):
        """
        Validates the guess against Hard Mode rules.
        Returns: (is_valid, error_message)
        """
        guess = guess.upper()
        
        # 1. Check Green Constraints
        for i, char in enumerate(self.hard_constraints['greens']):
            if char and guess[i] != char:
                return False, f"Hard Mode: Letter **{char}** must be at position {i+1}."
                
        # 2. Check Yellow Constraints
        for char in self.hard_constraints['present']:
            if char not in guess:
                return False, f"Hard Mode: Predicted letter **{char}** must be used."
                
        return True, ""

    def process_turn(self, guess: str, user):
        guess = guess.upper()
        self.last_interaction = datetime.datetime.now()
        
        pat = self.evaluate_guess(guess) 
        
        # --- Update Hard Mode Constraints ---
        # Parse pattern to update knowns
        # This assumes emojis are standard formatted block_<char>_<color>
        # But wait, evaluate_guess returns the emoji string.
        # We need the STATE list to easily update constraints.
        # Rerunning logic or extracting from evaluate_guess?
        # modify evaluate_guess to return state? No, avoid breaking changes.
        # Just re-calculate state locally. It's cheap.
        
        if self.hard_mode:
            secret_upper = self.secret.upper()
            s_list = list(secret_upper)
            g_list = list(guess)
            state_list = ["white"] * 5
            
            # Greens
            for i in range(5):
                if g_list[i] == s_list[i]:
                    state_list[i] = "green"
                    self.hard_constraints['greens'][i] = g_list[i] # Lock green
                    s_list[i] = None
                    g_list[i] = None
                    # Also remove from present set to avoid "must use" error if it's already green?
                    # Rule 2: "Yellow letter must be used".
                    # If I guess A (green), it satisfies "used".
                    # My constraints check 'char in guess'. So it's fine.
            
            # Yellows
            for i in range(5):
                if state_list[i] == "green": continue
                if g_list[i] is not None and g_list[i] in s_list:
                     self.hard_constraints['present'].add(g_list[i])
                     s_list[s_list.index(g_list[i])] = None # Consume
        # ------------------------------------
        
        self.history.append({'word': guess, 'pattern': pat, 'user': user})
        self.participants.add(user.id)
        self.guessed_words.add(guess)
        
        secret_upper = self.secret.upper()
        
        return pat, (guess == secret_upper), ((guess == secret_upper) or (self.attempts_used >= self.max_attempts))
