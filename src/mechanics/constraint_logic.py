import random
import re

class ConstraintGenerator:
    def __init__(self, secrets_dict, full_dict, valid_dict):
        """
        :param secrets_dict: Set of common/classic words used for puzzle generation.
        :param full_dict: Set of all valid words (dictionary).
        :param valid_dict: Set of valid 5-letter words for validation.
        """
        self.secrets_dict = secrets_dict 
        self.full_dict = full_dict
        self.valid_dict = valid_dict
        self.combined_dict = valid_dict | full_dict # All allowed guesses
        
        # Generation Pool: We only generate 5-letter questions from secrets_dict
        # and non-5-letter from full_dict (limited to common-ish words if possible)
        # For simplicity, we stick to secrets for 'five' and full for 'all'
        
        # Precompute for optimization
        self.vowels = frozenset('aeiou')
        self.consonants = frozenset('bcdfghjklmnpqrstvwxyz')
        
        # Cache for frequent operations
        self._vcv_words_cache = None
        self._double_letter_cache = {}
        
        # Build index for fast lookups
        self._build_indices()

    def _build_indices(self):
        """Build optimized indices for fast word lookups."""
        # Index words by length for quick filtering
        self.words_by_length = {}
        for word in self.combined_dict:
            length = len(word)
            if length not in self.words_by_length:
                self.words_by_length[length] = []
            self.words_by_length[length].append(word)
        
        # Index words by starting letter
        self.words_by_first_letter = {}
        for word in self.combined_dict:
            first = word[0]
            if first not in self.words_by_first_letter:
                self.words_by_first_letter[first] = []
            self.words_by_first_letter[first].append(word)
        
        # Index words by ending letter
        self.words_by_last_letter = {}
        for word in self.combined_dict:
            last = word[-1]
            if last not in self.words_by_last_letter:
                self.words_by_last_letter[last] = []
            self.words_by_last_letter[last].append(word)
        
        # Precompute VCV words for all words
        self._vcv_words_all = set()
        for word in self.combined_dict:
            if self._has_vcv_pattern(word):
                self._vcv_words_all.add(word)

    def _has_vcv_pattern(self, word):
        """Optimized VCV pattern check."""
        for i in range(len(word) - 2):
            if (word[i] in self.vowels and 
                word[i+1] in self.consonants and 
                word[i+2] in self.vowels):
                return True
        return False

    def generate_puzzle(self, max_attempts=50, force_unused_type=False, used_types=None, is_bonus=False, num_players=1):
        """
        Generates a constraint puzzle that has between 5 and 20 solutions.
        For bonus rounds, generates special puzzles with different mechanics.
        """
        if is_bonus:
            return self._generate_bonus_puzzle(num_players)
        
        weights = [
            (self._type_substring, 25, 'substring'),
            (self._type_substring_plus_letter, 15, 'substring_plus'),
            (self._type_letters_anywhere, 5, 'letters_anywhere'),
            (self._type_include_exclude, 5, 'include_exclude'),
            (self._type_double_letter, 5, 'double_letter'),
            (self._type_double_plus_letter, 2.5, 'double_plus'),
            (self._type_ends_with, 10, 'ends_with'),
            (self._type_start_end_same, 2.5, 'start_end_same'),
            (self._type_wordle_block, 20, 'wordle_block')
        ]
        
        # If forcing unused types, filter weights
        if force_unused_type and used_types:
            available_types = [(f, w, t) for f, w, t in weights if t not in used_types]
            if available_types:
                weights = available_types
        
        # Normalize weights
        total_weight = sum(w[1] for w in weights)
        
        for attempt in range(max_attempts):
            r = random.uniform(0, total_weight)
            current = 0
            selected_func = weights[0][0]
            selected_type = weights[0][2]
            
            for func, weight, ptype in weights:
                current += weight
                if r <= current:
                    selected_func = func
                    selected_type = ptype
                    break
            
            description, validator, visual, use_dict = selected_func()
            
            # 5-letter puzzles generate solutions only from the secret pool
            # to ensure they are "real" common words.
            target_dict = self.secrets_dict if use_dict == 'five' else self.combined_dict
            
            # Efficiently find solutions using validator
            solutions = self._fast_filter(target_dict, validator)
            
            if 5 <= len(solutions) <= 20:
                return {
                    'description': description,
                    'solutions': solutions,
                    'visual': visual,
                    'count': len(solutions),
                    'type': selected_type,
                    'five_letter_only': (use_dict == 'five'),
                    'multi_word': False
                }
        
        # Fallback - use simple ends_with
        return self._fallback_puzzle()

    def _generate_bonus_puzzle(self, num_players):
        """Generate special bonus round puzzles."""
        if num_players > 1:
            # Multi-player bonus: either longest word or most words
            bonus_type = random.choice(['longest_word', 'most_words'])
            
            if bonus_type == 'longest_word':
                # Random starting letter
                letter = random.choice('abcdefghijklmnoprstw')
                solutions = self.words_by_first_letter.get(letter, [])
                
                return {
                    'description': f"**Type the LONGEST word starting with {letter.upper()}**\n(Winner takes all!)",
                    'solutions': set(solutions),
                    'visual': None,
                    'count': len(solutions),
                    'type': 'longest_word',
                    'five_letter_only': False,
                    'multi_word': True
                }
            else:  # most_words
                # Letters anywhere challenge
                base_word = random.choice(list(self.secrets_dict))
                letters = random.sample(list(set(base_word)), 3)
                
                solutions = [w for w in self.combined_dict if all(l in w for l in letters)]
                
                return {
                    'description': f"**Type as many words as you can containing {', '.join(l.upper() for l in letters)}**\n(Most words wins!)",
                    'solutions': set(solutions),
                    'visual': None,
                    'count': len(solutions),
                    'type': 'most_words',
                    'five_letter_only': False,
                    'multi_word': True
                }
        else:
            # Single player: VCV bonus with all words
            solutions = list(self._vcv_words_all)
            
            return {
                'description': "**🎁 BONUS: Find a word with vowel-consonant-vowel pattern (VCV)**\n*Using the full dictionary!*",
                'solutions': set(solutions),
                'visual': None,
                'count': len(solutions),
                'type': 'vcv_bonus',
                'five_letter_only': False,
                'multi_word': False
            }

    def _fast_filter(self, dictionary, validator):
        """Optimized filtering using set operations when possible."""
        return set(w for w in dictionary if validator(w))

    def _fallback_puzzle(self):
        """Fallback puzzle that's guaranteed to work."""
        letter = random.choice('aeiorstn')
        solutions = set(w for w in self.secrets_dict if w.endswith(letter))
        visual = f"----{letter}"
        
        return {
            'description': f"Word ending with **{letter.upper()}**\n*(5-letter words only)*",
            'solutions': solutions,
            'visual': visual,
            'count': len(solutions),
            'type': 'ends_with',
            'five_letter_only': True,
            'multi_word': False
        }

    def _type_substring(self):
        """3-letter substring - 5-letter words only."""
        word = random.choice(list(self.secrets_dict))
        start = random.randint(0, 2)
        sub = word[start:start+3]
        desc = f"Word containing **{sub.upper()}** together\n*(5-letter words only)*"
        return desc, lambda w: len(w) == 5 and sub in w, None, 'all'

    def _type_substring_plus_letter(self):
        """2-letter substring + another letter - all words."""
        word = random.choice(list(self.secrets_dict))
        sub = word[0:2]
        other_word = random.choice(list(self.combined_dict))
        other_letter = random.choice([c for c in other_word if c not in sub])
        desc = f"Word containing **{sub.upper()}** with **{other_letter.upper()}** anywhere"
        return desc, lambda w: sub in w and other_letter in w, None, 'all'

    def _type_letters_anywhere(self):
        """3 letters anywhere - all words."""
        word = random.choice(list(self.secrets_dict))
        letters = random.sample(list(set(word)), min(3, len(set(word))))
        desc = f"Word containing **{', '.join(l.upper() for l in letters)}** (anywhere)"
        
        # Optimized validator using set operations
        letters_set = frozenset(letters)
        return desc, lambda w: letters_set.issubset(w), None, 'all'

    def _type_include_exclude(self):
        """Include certain letters, exclude others - all words."""
        word = random.choice(list(self.secrets_dict))
        include = random.sample(list(set(word)), min(2, len(set(word))))
        pool = [c for c in 'aeiorsnt' if c not in word]
        exclude = random.sample(pool, min(2, len(pool))) if len(pool) >= 2 else ['z', 'q']
        
        desc = f"Word with **{include[0].upper()}, {include[1].upper()}** but NO **{exclude[0].upper()}, {exclude[1].upper()}**"
        
        include_set = frozenset(include)
        exclude_set = frozenset(exclude)
        return desc, lambda w: include_set.issubset(w) and exclude_set.isdisjoint(w), None, 'all'

    def _type_double_letter(self):
        """Double letter - all words."""
        # Use cache
        if not self._double_letter_cache:
            for w in self.combined_dict:
                for i in range(len(w)-1):
                    if w[i] == w[i+1]:
                        double = w[i] * 2
                        if double not in self._double_letter_cache:
                            self._double_letter_cache[double] = []
                        self._double_letter_cache[double].append(w)
                        break
        
        double = random.choice(list(self._double_letter_cache.keys()))
        desc = f"Word containing double **{double.upper()[0]}**"
        return desc, lambda w: double in w, None, 'all'

    def _type_double_plus_letter(self):
        """Double letter + another letter - all words."""
        # Get a double
        if not self._double_letter_cache:
            self._type_double_letter()  # Build cache
        
        double = random.choice(list(self._double_letter_cache.keys()))
        other_word = random.choice(list(self.combined_dict))
        other_letter = random.choice([c for c in other_word if c not in double])
        
        desc = f"Word with double **{double[0].upper()}** and **{other_letter.upper()}** anywhere"
        return desc, lambda w: double in w and other_letter in w, None, 'all'

    def _type_ends_with(self):
        """Ends with letter - 5-letter words only."""
        letter = random.choice('aeiorstn')  # Common endings
        desc = f"Word ending with **{letter.upper()}**\n*(5-letter words only)*"
        visual = f"----{letter}"
        return desc, lambda w: len(w) == 5 and w.endswith(letter), visual, 'all'

    def _type_start_end_same(self):
        """Starts and ends with same letter + another letter - all words."""
        word = random.choice(list(self.secrets_dict))
        letter = word[0]
        other_word = random.choice(list(self.combined_dict))
        other_letter = random.choice([c for c in other_word if c != letter])
        
        desc = f"Word starting and ending with **{letter.upper()}**, with **{other_letter.upper()}**"
        return desc, lambda w: w[0] == letter and w[-1] == letter and other_letter in w, None, 'all'

    def _type_wordle_block(self):
        """Wordle-style pattern - 5-letter words only."""
        word = random.choice(list(self.secrets_dict))
        positions = random.sample(range(5), 2)
        pattern = ['-'] * 5
        
        for p in positions:
            pattern[p] = word[p]
        
        # Create visual
        visual = "".join(pattern)
        
        desc = f"Word matching pattern\n*(5-letter words only)*"
        
        def validator(w):
            if len(w) != 5:
                return False
            for i in range(5):
                if pattern[i] != '-' and w[i] != pattern[i]:
                    return False
            return True
            
        return desc, validator, visual, 'all'
