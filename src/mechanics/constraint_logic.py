import random
import re

class ConstraintGenerator:
    def __init__(self, dictionary):
        """
        :param dictionary: List or Set of valid 5-letter words.
        """
        self.dictionary = list(dictionary)
        self.vowels = set('aeiou')
        self.consonants = set('bcdfghjklmnpqrstvwxyz')

    def generate_puzzle(self, max_attempts=50):
        """
        Generates a constraint puzzle that has between 5 and 20 solutions.
        """
        weights = [
            (self._type_substring, 25),
            (self._type_substring_plus_letter, 15),
            (self._type_letters_anywhere, 5),
            (self._type_include_exclude, 5),
            (self._type_double_letter, 5),
            (self._type_double_plus_letter, 2.5),
            (self._type_ends_with, 10),
            (self._type_start_end_same, 2.5),
            (self._type_vcv_sandwich, 10),
            (self._type_wordle_block, 20)
        ]
        
        # Normalize weights
        total_weight = sum(w[1] for w in weights)
        
        for _ in range(max_attempts):
            r = random.uniform(0, total_weight)
            current = 0
            selected_func = weights[0][0]
            for func, weight in weights:
                current += weight
                if r <= current:
                    selected_func = func
                    break
            
            description, validator, visual = selected_func()
            
            # Efficiently find solutions
            solutions = [w for w in self.dictionary if validator(w)]
            
            if 5 <= len(solutions) <= 20:
                return {
                    'description': description,
                    'solutions': set(solutions),
                    'visual': visual,
                    'count': len(solutions)
                }
        
        # Fallback to a simple substring if multiple attempts fail
        return self.generate_puzzle(max_attempts=100) # Should eventually hit one

    def _type_substring(self):
        # Length 3 substring
        word = random.choice(self.dictionary)
        start = random.randint(0, 2)
        sub = word[start:start+3]
        desc = f"Word containing **{sub.upper()}** together"
        return desc, lambda w: sub in w, None

    def _type_substring_plus_letter(self):
        word = random.choice(self.dictionary)
        sub = word[0:2] # simple 2-letter sub
        # find another letter not in sub
        other_word = random.choice(self.dictionary)
        other_letter = random.choice([c for c in other_word if c not in sub])
        desc = f"Word containing **{sub.upper()}** with **{other_letter.upper()}** anywhere"
        return desc, lambda w: sub in w and other_letter in w, None

    def _type_letters_anywhere(self):
        word = random.choice(self.dictionary)
        letters = random.sample(list(set(word)), 3)
        desc = f"Word containing letters **{', '.join(l.upper() for l in letters)}** (anywhere)"
        return desc, lambda w: all(l in w for l in letters), None

    def _type_include_exclude(self):
        word = random.choice(self.dictionary)
        include = random.sample(list(set(word)), 2)
        # pick a common exclude letter (vowels/common consonants not in word)
        pool = [c for c in 'aeiorsnt' if c not in word]
        exclude = random.sample(pool, 2) if len(pool) >= 2 else ['z', 'q']
        desc = f"Word containing **{include[0].upper()}, {include[1].upper()}** but **NO {exclude[0].upper()}, {exclude[1].upper()}**"
        return desc, lambda w: all(i in w for i in include) and all(e not in w for e in exclude), None

    def _type_double_letter(self):
        # Find all words with doubles
        doubles = []
        for w in self.dictionary:
            for i in range(len(w)-1):
                if w[i] == w[i+1]:
                    doubles.append(w[i]*2)
                    break
        sub = random.choice(doubles) if doubles else "ll"
        desc = f"Word containing double **{sub.upper()[0]}**"
        return desc, lambda w: sub in w, None

    def _type_double_plus_letter(self):
        # Similar to above
        desc, val, _ = self._type_double_letter()
        # extract the double
        double = desc.split("**")[1].lower()
        other_word = random.choice(self.dictionary)
        other_letter = random.choice([c for c in other_word if c not in double])
        desc = f"Word containing double **{double[0].upper()}** with **{other_letter.upper()}** anywhere"
        return desc, lambda w: double in w and other_letter in w, None

    def _type_ends_with(self):
        word = random.choice(self.dictionary)
        letter = word[-1]
        desc = f"Word ending with **{letter.upper()}**"
        # Visual: 4 black squares + 1 green letter
        visual = f"⬛⬛⬛⬛🟩**{letter.upper()}**"
        return desc, lambda w: w.endswith(letter), visual

    def _type_start_end_same(self):
        word = random.choice(self.dictionary)
        letter = word[0]
        # find another letter
        other_word = random.choice(self.dictionary)
        other_letter = random.choice([c for c in other_word if c != letter])
        desc = f"Word starting and ending with **{letter.upper()}**, with **{other_letter.upper()}** somewhere"
        return desc, lambda w: w.startswith(letter) and w.endswith(letter) and other_letter in w, None

    def _type_vcv_sandwich(self):
        desc = "Word containing a **vowel–consonant–vowel** pattern (VCV)"
        def validator(w):
            for i in range(len(w)-2):
                if w[i] in self.vowels and w[i+1] in self.consonants and w[i+2] in self.vowels:
                    return True
            return False
        return desc, validator, None

    def _type_wordle_block(self):
        word = random.choice(self.dictionary)
        # pick 2 positions to fix
        pos = random.sample(range(5), 2)
        pattern = list("-----")
        for p in pos:
            pattern[p] = word[p]
        
        # Create visual
        visual_list = []
        for c in pattern:
            if c == '-': visual_list.append("⬛")
            else: visual_list.append(f"🟩**{c.upper()}**")
        visual = "".join(visual_list)
        
        desc = f"Word matching pattern: **{''.join(pattern).upper()}**"
        
        def validator(w):
            for i in range(5):
                if pattern[i] != '-' and w[i] != pattern[i]:
                    return False
            return True
            
        return desc, validator, visual
