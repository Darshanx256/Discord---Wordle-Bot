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
        self.bad_combos = set() # (type, key) to skip forever
        
        # Build index for fast lookups
        self._build_indices()

    def _build_indices(self):
        """Build optimized indices for fast word lookups."""
        self.words_by_length = {}
        self.words_by_first_letter = {l: set() for l in 'abcdefghijklmnopqrstuvwxyz'}
        self.words_by_last_letter = {l: set() for l in 'abcdefghijklmnopqrstuvwxyz'}
        self.words_containing_letter = {l: set() for l in 'abcdefghijklmnopqrstuvwxyz'}
        
        for word in self.combined_dict:
            length = len(word)
            if length not in self.words_by_length:
                self.words_by_length[length] = []
            self.words_by_length[length].append(word)
            
            first = word[0]
            if first in self.words_by_first_letter:
                self.words_by_first_letter[first].add(word)
            
            last = word[-1]
            if last in self.words_by_last_letter:
                self.words_by_last_letter[last].add(word)
                
            for char in set(word):
                if char in self.words_containing_letter:
                    self.words_containing_letter[char].add(word)
        
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
        Generates a constraint puzzle that has strictly [5, 20] solutions.
        Returns validator function for on-the-fly checking.
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
        
        if force_unused_type and used_types:
            available_types = [(f, w, t) for f, w, t in weights if t not in used_types]
            if available_types:
                weights = available_types
        
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
            
            # Get puzzle data and identify key
            description, validator, visual, use_dict, combo_key = selected_func()
            
            # Skip if known bad combo
            full_cache_key = f"{selected_type}:{combo_key}"
            if full_cache_key in self.bad_combos:
                continue

            target_dict = self.secrets_dict if use_dict == 'five' else self.combined_dict
            
            # Optimized range check: stop at 21 (reject if > 20)
            valid, count = self._check_solution_range(validator, target_dict, min_val=5, max_val=20)
            
            if valid:
                return {
                    'description': description,
                    'validator': validator,
                    'visual': visual,
                    'type': selected_type,
                    'five_letter_only': (use_dict == 'five'),
                    'multi_word': False
                }
            else:
                # Cache bad combo to save time in future attempts
                self.bad_combos.add(full_cache_key)
        
        return self._fallback_puzzle()

    def _check_solution_range(self, validator, dictionary, min_val=5, max_val=20):
        """Stops scanning as soon as it goes over max_val or dictionary ends."""
        count = 0
        for w in dictionary:
            if validator(w):
                count += 1
                if count > max_val:
                    return False, count
        
        return (count >= min_val), count

    def _generate_bonus_puzzle(self, num_players):
        """Generate special bonus round puzzles."""
        if num_players > 1:
            bonus_type = random.choice(['longest_word', 'most_words'])
            
            if bonus_type == 'longest_word':
                letter = random.choice('abcdefghijklmnoprstw')
                # For high-speed lookups
                
                return {
                    'description': f"**Type the LONGEST word starting with {letter.upper()}**\n(Winner takes all!)",
                    'validator': lambda w: w[0].lower() == letter,
                    'solutions': self.words_by_first_letter.get(letter, set()), # Still needed for multi-word scoring
                    'visual': None,
                    'type': 'longest_word',
                    'five_letter_only': False,
                    'multi_word': True
                }
            else:  # most_words
                base_word = random.choice(list(self.secrets_dict))
                letters = random.sample(list(set(base_word)), 3)
                
                # Optimized solutions using set intersections
                solutions = self.combined_dict
                for l in letters:
                    solutions = solutions & self.words_containing_letter.get(l, set())
                
                return {
                    'description': f"**Type as many words as you can containing {', '.join(l.upper() for l in letters)}**\n(Most words wins!)",
                    'validator': lambda w: all(l in w for l in letters),
                    'solutions': solutions,
                    'visual': None,
                    'type': 'most_words',
                    'five_letter_only': False,
                    'multi_word': True
                }
        else:
            return {
                'description': "**🎁 BONUS: Find a word with vowel-consonant-vowel pattern (VCV)**\n*Using the full dictionary!*",
                'validator': lambda w: w in self._vcv_words_all,
                'visual': None,
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
        visual = f"----{letter}"
        
        return {
            'description': f"Word ending with **{letter.upper()}**\n*(5-letter words only)*",
            'validator': lambda w: len(w) == 5 and w.endswith(letter),
            'visual': visual,
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
        return desc, lambda w: len(w) == 5 and sub in w, None, 'five', sub

    def _type_substring_plus_letter(self):
        """2-letter substring + another letter - all words."""
        word = random.choice(list(self.secrets_dict))
        sub = word[0:2]
        other_word = random.choice(list(self.combined_dict))
        other_letter = random.choice([c for c in other_word if c not in sub])
        desc = f"Word containing **{sub.upper()}** with **{other_letter.upper()}** anywhere\n*(5 or MORE letter words)*"
        return desc, lambda w: sub in w and other_letter in w, None, 'all', f"{sub}+{other_letter}"

    def _type_letters_anywhere(self):
        """3 letters anywhere - all words."""
        word = random.choice(list(self.secrets_dict))
        letters = random.sample(list(set(word)), min(3, len(set(word))))
        desc = f"Word containing **{', '.join(l.upper() for l in letters)}** (anywhere)\n*(5 or MORE letter words)*"
        
        # Optimized validator using set operations
        letters_set = frozenset(letters)
        return desc, lambda w: letters_set.issubset(w), None, 'all', "".join(sorted(letters))

    def _type_include_exclude(self):
        """Include certain letters, exclude others - all words."""
        word = random.choice(list(self.secrets_dict))
        include = random.sample(list(set(word)), min(2, len(set(word))))
        pool = [c for c in 'aeiorsnt' if c not in word]
        exclude = random.sample(pool, min(2, len(pool))) if len(pool) >= 2 else ['z', 'q']
        
        desc = f"Word with **{include[0].upper()}, {include[1].upper()}** but NO **{exclude[0].upper()}, {exclude[1].upper()}**\n*(5 or MORE letter words)*"
        
        include_set = frozenset(include)
        exclude_set = frozenset(exclude)
        key = f"inc:{''.join(sorted(include))}|exc:{''.join(sorted(exclude))}"
        return desc, lambda w: include_set.issubset(w) and exclude_set.isdisjoint(w), None, 'all', key

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
        desc = f"Word containing double **{double.upper()[0]}**\n*(5 or MORE letter words)*"
        return desc, lambda w: double in w, None, 'all', double

    def _type_double_plus_letter(self):
        """Double letter + another letter - all words."""
        # Get a double
        if not self._double_letter_cache:
            self._type_double_letter()  # Build cache
        
        double = random.choice(list(self._double_letter_cache.keys()))
        other_word = random.choice(list(self.combined_dict))
        other_letter = random.choice([c for c in other_word if c not in double])
        
        desc = f"Word with double **{double[0].upper()}** and **{other_letter.upper()}** anywhere\n*(5 or MORE letter words)*"
        return desc, lambda w: double in w and other_letter in w, None, 'all', f"{double}+{other_letter}"

    def _type_ends_with(self):
        """Ends with letter - 5-letter words only."""
        letter = random.choice('aeiorstn')  # Common endings
        desc = f"Word ending with **{letter.upper()}**\n*(5-letter words only)*"
        visual = f"----{letter}"
        return desc, lambda w: len(w) == 5 and w.endswith(letter), visual, 'five', letter

    def _type_start_end_same(self):
        """Starts and ends with same letter + another letter - all words."""
        word = random.choice(list(self.secrets_dict))
        letter = word[0]
        other_word = random.choice(list(self.combined_dict))
        other_letter = random.choice([c for c in other_word if c != letter])
        
        desc = f"Word starting and ending with **{letter.upper()}**, with **{other_letter.upper()}**\n*(5 or MORE letter words)*"
        return desc, lambda w: w[0] == letter and w[-1] == letter and other_letter in w, None, 'all', f"{letter}...{letter}+{other_letter}"

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
            
        return desc, validator, visual, 'five', visual
