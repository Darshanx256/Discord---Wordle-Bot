"""
Memory-optimized constraint puzzle generator.
Uses pre-generated puzzles from word_rush_puzzles.txt for instant puzzle selection.
WordNet is only loaded when needed for synonyms/antonyms validation.
"""
import random
import json
import os
from nltk.corpus import wordnet

# Lazy load WordNet only when needed
_wordnet_loaded = False

def _ensure_wordnet():
    """Lazy load WordNet only when needed for synonyms/antonyms."""
    global _wordnet_loaded
    if _wordnet_loaded:
        return
    
    import nltk
    try:
        nltk.data.find('corpora/wordnet')
    except LookupError:
        nltk.download('wordnet', quiet=True)
    try:
        nltk.data.find('corpora/omw-1.4')
    except LookupError:
        nltk.download('omw-1.4', quiet=True)
    
    _wordnet_loaded = True

# Pre-generated puzzles cache
_cached_puzzles = None
_cached_dictionary_set = None
_cached_dictionary_list = None

def _load_puzzles():
    """Load pre-generated puzzles from file."""
    global _cached_puzzles
    if _cached_puzzles is not None:
        return _cached_puzzles
    
    # Try multiple possible paths
    possible_paths = [
        "word_rush_puzzles.txt",  # Root directory
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "word_rush_puzzles.txt"),  # Project root
    ]
    
    puzzle_file = None
    for path in possible_paths:
        if os.path.exists(path):
            puzzle_file = path
            break
    
    puzzles = []
    
    if puzzle_file:
        try:
            with open(puzzle_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        puzzles.append(json.loads(line))
            print(f"✅ Loaded {len(puzzles)} pre-generated puzzles from {puzzle_file}")
        except Exception as e:
            print(f"⚠️ Failed to load puzzles: {e}. Falling back to runtime generation.")
            puzzles = []
    else:
        print(f"⚠️ Puzzle file {puzzle_file} not found. Run generate_word_rush_puzzles.py first.")
        puzzles = []
    
    _cached_puzzles = puzzles
    return puzzles

def _build_dictionary():
    """
    Build dictionary for word validation only.
    Much smaller memory footprint - only used for validation, not generation.
    """
    global _cached_dictionary_set, _cached_dictionary_list
    if _cached_dictionary_set is not None:
        return _cached_dictionary_list
    
    import nltk
    from nltk.corpus import words as nltk_words
    
    try:
        nltk.data.find('corpora/words')
    except LookupError:
        nltk.download('words', quiet=True)
    
    print("🔍 Building validation dictionary...")
    word_set = {
        w.lower() for w in nltk_words.words() 
        if 4 <= len(w) <= 8 and w.isalpha() and w.lower().isalpha()
    }
    
    _cached_dictionary_set = word_set
    _cached_dictionary_list = sorted(list(word_set))
    
    print(f"✅ Dictionary: {len(_cached_dictionary_list)} words")
    return _cached_dictionary_list

class ConstraintGenerator:
    def __init__(self, dictionary=None):
        """
        Memory-optimized generator using pre-generated puzzles.
        :param dictionary: Optional custom dictionary. If None, uses NLTK words.
        """
        if dictionary is None:
            self.dictionary = _build_dictionary()
            self.dictionary_set = _cached_dictionary_set
        else:
            self.dictionary = list(dictionary)
            self.dictionary_set = set(dictionary)
        
        self.puzzles = _load_puzzles()
        self.vowels = set('aeiou')
        self.consonants = set('bcdfghjklmnpqrstvwxyz')

    def generate_puzzle(self, max_attempts=100):
        """
        Select a random pre-generated puzzle.
        Falls back to simple generation if no puzzles available.
        """
        if self.puzzles:
            puzzle_data = random.choice(self.puzzles)
            return self._create_puzzle_from_data(puzzle_data)
        
        # Fallback: simple substring puzzle if no pre-generated puzzles
        return self._generate_fallback_puzzle()

    def _create_puzzle_from_data(self, puzzle_data):
        """Create puzzle object from pre-generated data."""
        ptype = puzzle_data['type']
        description = puzzle_data['description']
        constraint = puzzle_data['constraint']
        expected_length = puzzle_data['expected_length']
        visual = puzzle_data.get('visual')
        
        # Create validator based on puzzle type
        validator = self._create_validator(ptype, constraint, expected_length, puzzle_data)
        
        return {
            'description': description,
            'solutions': None,  # Don't store solutions - validate on-the-fly
            'visual': visual,
            'expected_length': expected_length,
            'type': ptype,
            'constraint': constraint,
            'validator': validator,
            'count': puzzle_data.get('count', 0)
        }

    def _create_validator(self, ptype, constraint, expected_length, puzzle_data):
        """Create validation function for puzzle type."""
        if ptype == 'substring':
            sub = constraint.lower()
            return lambda w: len(w) == expected_length and sub in w
        
        elif ptype == 'letters_anywhere':
            letters = constraint if isinstance(constraint, list) else [constraint]
            return lambda w: len(w) == expected_length and all(l in w for l in letters)
        
        elif ptype == 'ends_with':
            letter = constraint.lower()
            return lambda w: len(w) == expected_length and w.endswith(letter)
        
        elif ptype == 'starts_with':
            letter = constraint.lower()
            return lambda w: len(w) == expected_length and w.startswith(letter)
        
        elif ptype == 'double_letter':
            double = constraint.lower()
            return lambda w: len(w) == expected_length and double in w
        
        elif ptype == 'wordle_block':
            pattern = constraint if isinstance(constraint, list) else list(constraint)
            return lambda w: self._matches_pattern(w, pattern)
        
        elif ptype == 'rhyme':
            ending = constraint.lower()
            return lambda w: len(w) == expected_length and w.endswith(ending)
        
        elif ptype == 'jumble':
            target_word = constraint.lower()  # The actual word
            return lambda w: len(w) == expected_length and sorted(w) == sorted(target_word)
        
        elif ptype == 'synonym':
            # Lazy load WordNet for synonyms
            _ensure_wordnet()
            base_word = constraint.lower()
            synonyms = set()
            for synset in wordnet.synsets(base_word):
                for lemma in synset.lemmas():
                    syn_word = lemma.name().replace('_', '').lower()
                    if 4 <= len(syn_word) <= 8 and syn_word.isalpha() and syn_word != base_word:
                        synonyms.add(syn_word)
            return lambda w: len(w) == expected_length and w in synonyms
        
        elif ptype == 'opposite':
            # Lazy load WordNet for antonyms
            _ensure_wordnet()
            base_word = constraint.lower()
            antonyms = set()
            for synset in wordnet.synsets(base_word):
                for lemma in synset.lemmas():
                    for ant in lemma.antonyms():
                        ant_word = ant.name().replace('_', '').lower()
                        if 4 <= len(ant_word) <= 8 and ant_word.isalpha():
                            antonyms.add(ant_word)
            return lambda w: len(w) == expected_length and w in antonyms
        
        else:
            # Default: accept any word of correct length
            return lambda w: len(w) == expected_length

    def _matches_pattern(self, word, pattern):
        """Check if word matches pattern (e.g., 'a----' or ['a', '-', '-', '-', '-'])."""
        if len(word) != len(pattern):
            return False
        for i, char in enumerate(pattern):
            if char != '-' and word[i] != char.lower():
                return False
        return True

    def _generate_fallback_puzzle(self):
        """Fallback puzzle generation if no pre-generated puzzles available."""
        if not self.dictionary:
            return {
                'description': "Word containing **ER** together",
                'solutions': None,
                'visual': None,
                'expected_length': 5,
                'type': 'substring',
                'constraint': 'er',
                'validator': lambda w: len(w) == 5 and 'er' in w,
                'count': 0
            }
        
        word = random.choice(self.dictionary)
        sub = word[:2] if len(word) >= 2 else word[0]
        return {
            'description': f"Word containing **{sub.upper()}** together",
            'solutions': None,
            'visual': None,
            'expected_length': len(word),
            'type': 'substring',
            'constraint': sub,
            'validator': lambda w, s=sub: len(w) == len(word) and s in w,
            'count': 0
        }

    def validate_guess(self, word, puzzle):
        """
        Validate a user's guess against the puzzle.
        Returns (is_valid, is_solution)
        """
        word_lower = word.lower()
        
        # Check if word is in dictionary
        if word_lower not in self.dictionary_set:
            return False, False
        
        # Check expected length
        if len(word_lower) != puzzle['expected_length']:
            return False, False
        
        # Check if word matches constraint
        validator = puzzle.get('validator')
        if validator:
            try:
                is_solution = validator(word_lower)
                return True, is_solution
            except Exception as e:
                print(f"⚠️ Validation error: {e}")
                return False, False
        
        return False, False
