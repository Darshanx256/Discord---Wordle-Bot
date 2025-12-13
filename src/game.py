import datetime
import discord
from src.utils import EMOJIS

# ========= 4. GAME CLASS =========
class WordleGame:
    __slots__ = ('secret', 'secret_set', 'channel_id', 'started_by', 'max_attempts', 'history', 
                 'used_letters', 'participants', 'guessed_words', 'last_interaction', 'message_id')

    def __init__(self, secret: str, channel_id: int, started_by: discord.abc.User, message_id: int):
        self.secret = secret
        self.secret_set = set(secret) # Store secret as a set for O(1) lookups
        self.channel_id = channel_id
        self.started_by = started_by
        self.max_attempts = 6
        self.history = [] 
        self.participants = set() 
        self.guessed_words = set()
        self.used_letters = {'correct': set(), 'present': set(), 'absent': set()}
        self.last_interaction = datetime.datetime.now()
        self.start_time = datetime.datetime.now()
        self.message_id = message_id

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

    def process_turn(self, guess: str, user):
        guess = guess.upper()
        self.last_interaction = datetime.datetime.now()
        
        pat = self.evaluate_guess(guess) 
        
        self.history.append({'word': guess, 'pattern': pat, 'user': user})
        self.participants.add(user.id)
        self.guessed_words.add(guess)
        
        secret_upper = self.secret.upper()
        
        return pat, (guess == secret_upper), ((guess == secret_upper) or (self.attempts_used >= self.max_attempts))
