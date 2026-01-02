import random
import re
import nltk
from nltk.corpus import words as nltk_words
from nltk.corpus import wordnet

# Lazy load NLTK data
try:
    nltk.data.find('corpora/words')
except LookupError:
    nltk.download('words', quiet=True)
try:
    nltk.data.find('corpora/wordnet')
except LookupError:
    nltk.download('wordnet', quiet=True)
try:
    nltk.data.find('corpora/omw-1.4')
except LookupError:
    nltk.download('omw-1.4', quiet=True)

class ConstraintGenerator:
    def __init__(self, dictionary=None):
        """
        :param dictionary: List or Set of valid words. If None, uses NLTK full English words (4-8 letters).
        """
        if dictionary is None:
            # Use NLTK for full dictionary, filtered to 4-8 letter English words
            self.dictionary = [w.lower() for w in nltk_words.words() if 4 <= len(w) <= 8 and w.isalpha() and w.lower() in wordnet._lemma_from_key]
        else:
            self.dictionary = list(dictionary)
        self.vowels = set('aeiou')
        self.consonants = set('bcdfghjklmnpqrstvwxyz')

    def generate_puzzle(self, max_attempts=100):
        """
        Generates a constraint puzzle that has between 5 and 20 solutions.
        Supports variable word lengths (4-8 letters).
        """
        weights = [
            (self._type_substring, 12),              # Adjusted for variety
            (self._type_substring_plus_letter, 15),
            (self._type_letters_anywhere, 10),
            (self._type_include_exclude, 10),
            (self._type_double_letter, 8),
            (self._type_double_plus_letter, 6),
            (self._type_ends_with, 10),
            (self._type_starts_with, 8),
            (self._type_start_end_same, 4),
            (self._type_wordle_block, 12),
            (self._type_rhyme, 12),                  # New: Rhyming
            (self._type_opposite, 6),                # New: Opposites
            (self._type_synonym, 10),                # New: Similar (synonyms)
            (self._type_jumble, 15)                  # New: Jumble
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
            
            description, validator, visual, expected_length = selected_func()
            
            # Efficiently find solutions
            solutions = [w for w in self.dictionary if validator(w) and len(w) == expected_length]
            
            if 5 <= len(solutions) <= 20:
                return {
                    'description': description,
                    'solutions': set(solutions),
                    'visual': visual,
                    'expected_length': expected_length,
                    'count': len(solutions)
                }
        
        # Fallback to a simple substring if multiple attempts fail
        desc, val, _, el = self._type_substring()
        solutions = [w for w in self.dictionary if val(w) and len(w) == el]
        return {
            'description': desc,
            'solutions': set(solutions),
            'visual': None,
            'expected_length': el,
            'count': len(solutions)
        }

    def _type_substring(self):
        # Variable length substring (2-3 letters)
        word = random.choice(self.dictionary)
        length = random.choice([2, 3])
        start = random.randint(0, len(word) - length)
        sub = word[start:start+length]
        desc = f"Word containing **{sub.upper()}** together"
        expected_length = len(word)
        return desc, lambda w: sub in w, None, expected_length

    def _type_substring_plus_letter(self):
        word = random.choice(self.dictionary)
        length = random.choice([2, 3])
        sub = word[:length]
        other_word = random.choice(self.dictionary)
        other_letter = random.choice([c for c in other_word if c not in sub])
        desc = f"Word containing **{sub.upper()}** with **{other_letter.upper()}** anywhere"
        expected_length = len(word)
        return desc, lambda w: sub in w and other_letter in w, None, expected_length

    def _type_letters_anywhere(self):
        word = random.choice(self.dictionary)
        num_letters = random.choice([2, 3])
        letters = random.sample(list(set(word)), num_letters)
        desc = f"Word containing **{', '.join(l.upper() for l in letters)}** (anywhere)"
        expected_length = len(word)
        return desc, lambda w: all(l in w for l in letters), None, expected_length

    def _type_include_exclude(self):
        word = random.choice(self.dictionary)
        num_include = random.choice([1, 2])
        include = random.sample(list(set(word)), num_include)
        pool = [c for c in 'aeiorsnt' if c not in word]
        num_exclude = random.choice([1, 2])
        exclude = random.sample(pool, num_exclude) if len(pool) >= num_exclude else ['z', 'q'][:num_exclude]
        desc = f"Word with **{', '.join(i.upper() for i in include)}** but NO **{', '.join(e.upper() for e in exclude)}**"
        expected_length = len(word)
        return desc, lambda w: all(i in w for i in include) and all(e not in w for e in exclude), None, expected_length

    def _type_double_letter(self):
        doubles = []
        for w in self.dictionary:
            for i in range(len(w)-1):
                if w[i] == w[i+1]:
                    doubles.append(w[i]*2)
                    break
        sub = random.choice(doubles) if doubles else "ll"
        desc = f"Word with double **{sub.upper()[0]}**"
        word = random.choice(self.dictionary)  # For length
        expected_length = len(word)
        return desc, lambda w: sub in w, None, expected_length

    def _type_double_plus_letter(self):
        desc, val, _, expected_length = self._type_double_letter()
        double = re.search(r'\*\*(.+)\*\*', desc).group(1).lower()
        other_word = random.choice(self.dictionary)
        other_letter = random.choice([c for c in other_word if c not in double])
        desc = f"Word with double **{double[0].upper()}** and **{other_letter.upper()}** anywhere"
        return desc, lambda w: double in w and other_letter in w, None, expected_length

    def _type_ends_with(self):
        word = random.choice(self.dictionary)
        letter = word[-1]
        desc = f"Word ending with **{letter.upper()}**"
        visual = f"----{letter}" if len(word)==5 else f"{'-'*(len(word)-1)}{letter}"
        return desc, lambda w: w.endswith(letter), visual, len(word)

    def _type_starts_with(self):
        word = random.choice(self.dictionary)
        letter = word[0]
        desc = f"Word starting with **{letter.upper()}**"
        visual = f"{letter}----" if len(word)==5 else f"{letter}{'-'*(len(word)-1)}"
        return desc, lambda w: w.startswith(letter), visual, len(word)

    def _type_start_end_same(self):
        word = random.choice(self.dictionary)
        letter = word[0]
        other_word = random.choice(self.dictionary)
        other_letter = random.choice([c for c in other_word if c != letter])
        desc = f"Word starting and ending with **{letter.upper()}**, containing **{other_letter.upper()}**"
        return desc, lambda w: w.startswith(letter) and w.endswith(letter) and other_letter in w, None, len(word)

    def _type_wordle_block(self):
        word = random.choice(self.dictionary)
        num_fixed = random.choice([1, 2])
        pos = random.sample(range(len(word)), num_fixed)
        pattern = ['-'] * len(word)
        for p in pos:
            pattern[p] = word[p]
        
        visual = "".join(pattern)
        desc = f"Word matching pattern"
        
        def validator(w):
            if len(w) != len(pattern): return False
            for i in range(len(pattern)):
                if pattern[i] != '-' and w[i] != pattern[i]:
                    return False
            return True
            
        return desc, validator, visual, len(word)

    def _type_rhyme(self):
        """Rhyming words: same ending sound (last 2-3 letters)."""
        word = random.choice(self.dictionary)
        rhyme_len = random.choice([2, 3])
        ending = word[-rhyme_len:]
        desc = f"Words rhyming with **{word.upper()}**"
        expected_length = len(word)
        return desc, lambda w: w.endswith(ending), None, expected_length

    def _type_opposite(self):
        """Words that are opposites of a base word (using WordNet antonyms)."""
        attempts = 0
        while attempts < 20:
            base = random.choice(self.dictionary)
            antonyms = set()
            for synset in wordnet.synsets(base):
                for lemma in synset.lemmas():
                    for ant in lemma.antonyms():
                        ant_word = ant.name().replace('_', '').lower()
                        if 4 <= len(ant_word) <= 8 and ant_word.isalpha():
                            antonyms.add(ant_word)
            if len(antonyms & set(self.dictionary)) >= 5:
                desc = f"Word opposite of **{base.upper()}**"
                expected_length = len(base)  # Approximate, antonyms may vary slightly
                def validator(w):
                    # Recompute for consistency
                    return w in antonyms
                return desc, validator, None, expected_length
            attempts += 1
        # Fallback to simple exclude
        return self._type_include_exclude()

    def _type_synonym(self):
        """Words similar to (synonyms of) a base word (using WordNet)."""
        attempts = 0
        while attempts < 20:
            base = random.choice(self.dictionary)
            synonyms = set()
            for synset in wordnet.synsets(base):
                for lemma in synset.lemmas():
                    syn_word = lemma.name().replace('_', '').lower()
                    if 4 <= len(syn_word) <= 8 and syn_word.isalpha() and syn_word != base:
                        synonyms.add(syn_word)
            if len(synonyms & set(self.dictionary)) >= 5:
                desc = f"Word similar to **{base.upper()}**"
                expected_length = len(base)
                def validator(w):
                    return w in synonyms
                return desc, validator, None, expected_length
            attempts += 1
        # Fallback
        return self._type_letters_anywhere()

    def _type_jumble(self):
        """Solve the jumble: unscramble letters."""
        word = random.choice(self.dictionary)
        scrambled = ''.join(random.sample(word, len(word)))
        desc = f"Unscramble: **{scrambled.upper()}**"
        expected_length = len(word)
        visual = scrambled.upper()
        return desc, lambda w: sorted(w) == sorted(word), visual, expected_length
