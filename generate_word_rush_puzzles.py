"""
One-time script to pre-generate Word Rush puzzles.
Generates 200-300 puzzles following the 5-20 solutions rule.
Run this once to create word_rush_puzzles.txt
"""
import random
import re
import nltk
from nltk.corpus import words as nltk_words
from nltk.corpus import wordnet
import json

# Lazy load NLTK data
try:
    nltk.data.find('corpora/words')
except LookupError:
    nltk.download('words', quiet=True)
try:
    nltk.data.find('corpora/wordnet')
except LookupError:
    nltk.download('wordnet', quiet=True)

print("🔍 Building dictionary...")
# Build efficient dictionary (4-8 letters)
dictionary = sorted(list({
    w.lower() for w in nltk_words.words() 
    if 4 <= len(w) <= 8 and w.isalpha() and w.lower().isalpha()
}))
print(f"✅ Dictionary: {len(dictionary)} words")

def generate_puzzle_type_substring(dict_list):
    """Word containing substring together"""
    word = random.choice(dict_list)
    length = random.choice([2, 3])
    start = random.randint(0, len(word) - length)
    sub = word[start:start+length]
    expected_length = len(word)
    solutions = [w for w in dict_list if sub in w and len(w) == expected_length]
    if 5 <= len(solutions) <= 20:
        return {
            'type': 'substring',
            'description': f"Word containing **{sub.upper()}** together",
            'constraint': sub,
            'expected_length': expected_length,
            'count': len(solutions)
        }
    return None

def generate_puzzle_type_letters_anywhere(dict_list):
    """Word containing letters anywhere"""
    word = random.choice(dict_list)
    num_letters = random.choice([2, 3])
    letters = random.sample(list(set(word)), num_letters)
    expected_length = len(word)
    solutions = [w for w in dict_list if all(l in w for l in letters) and len(w) == expected_length]
    if 5 <= len(solutions) <= 20:
        return {
            'type': 'letters_anywhere',
            'description': f"Word containing **{', '.join(l.upper() for l in letters)}** (anywhere)",
            'constraint': letters,
            'expected_length': expected_length,
            'count': len(solutions)
        }
    return None

def generate_puzzle_type_ends_with(dict_list):
    """Word ending with letter"""
    word = random.choice(dict_list)
    letter = word[-1]
    expected_length = len(word)
    solutions = [w for w in dict_list if w.endswith(letter) and len(w) == expected_length]
    if 5 <= len(solutions) <= 20:
        visual = f"----{letter}" if expected_length==5 else f"{'-'*(expected_length-1)}{letter}"
        return {
            'type': 'ends_with',
            'description': f"Word ending with **{letter.upper()}**",
            'constraint': letter,
            'expected_length': expected_length,
            'visual': visual,
            'count': len(solutions)
        }
    return None

def generate_puzzle_type_starts_with(dict_list):
    """Word starting with letter"""
    word = random.choice(dict_list)
    letter = word[0]
    expected_length = len(word)
    solutions = [w for w in dict_list if w.startswith(letter) and len(w) == expected_length]
    if 5 <= len(solutions) <= 20:
        visual = f"{letter}----" if expected_length==5 else f"{letter}{'-'*(expected_length-1)}"
        return {
            'type': 'starts_with',
            'description': f"Word starting with **{letter.upper()}**",
            'constraint': letter,
            'expected_length': expected_length,
            'visual': visual,
            'count': len(solutions)
        }
    return None

def generate_puzzle_type_double_letter(dict_list):
    """Word with double letter"""
    doubles = []
    for w in dict_list:
        for i in range(len(w)-1):
            if w[i] == w[i+1]:
                doubles.append((w[i]*2, len(w)))
                break
    
    if not doubles:
        return None
    
    sub, expected_length = random.choice(doubles)
    solutions = [w for w in dict_list if sub in w and len(w) == expected_length]
    if 5 <= len(solutions) <= 20:
        return {
            'type': 'double_letter',
            'description': f"Word with double **{sub.upper()[0]}**",
            'constraint': sub,
            'expected_length': expected_length,
            'count': len(solutions)
        }
    return None

def generate_puzzle_type_wordle_block(dict_list):
    """Word matching pattern"""
    word = random.choice(dict_list)
    num_fixed = random.choice([1, 2])
    pos = random.sample(range(len(word)), num_fixed)
    pattern = ['-'] * len(word)
    for p in pos:
        pattern[p] = word[p]
    
    visual = "".join(pattern)
    expected_length = len(word)
    
    def matches_pattern(w):
        if len(w) != len(pattern):
            return False
        for i in range(len(pattern)):
            if pattern[i] != '-' and w[i] != pattern[i]:
                return False
        return True
    
    solutions = [w for w in dict_list if matches_pattern(w) and len(w) == expected_length]
    if 5 <= len(solutions) <= 20:
        return {
            'type': 'wordle_block',
            'description': f"Word matching pattern",
            'constraint': pattern,
            'expected_length': expected_length,
            'visual': visual,
            'count': len(solutions)
        }
    return None

def generate_puzzle_type_rhyme(dict_list):
    """Words rhyming (same ending)"""
    word = random.choice(dict_list)
    rhyme_len = random.choice([2, 3])
    ending = word[-rhyme_len:]
    expected_length = len(word)
    solutions = [w for w in dict_list if w.endswith(ending) and len(w) == expected_length]
    if 5 <= len(solutions) <= 20:
        return {
            'type': 'rhyme',
            'description': f"Words rhyming with **{word.upper()}**",
            'constraint': ending,
            'expected_length': expected_length,
            'count': len(solutions)
        }
    return None

def generate_puzzle_type_jumble(dict_list):
    """Unscramble letters"""
    word = random.choice(dict_list)
    scrambled = ''.join(random.sample(word, len(word)))
    expected_length = len(word)
    solutions = [w for w in dict_list if sorted(w) == sorted(word) and len(w) == expected_length]
    if len(solutions) == 1:  # Jumble should have exactly 1 solution
        return {
            'type': 'jumble',
            'description': f"Unscramble: **{scrambled.upper()}**",
            'constraint': word,  # Store the actual word for validation
            'scrambled': scrambled,
            'expected_length': expected_length,
            'visual': scrambled.upper(),
            'count': 1
        }
    return None

def generate_puzzle_type_synonym(dict_list):
    """Words similar to (synonyms of) a base word."""
    attempts = 0
    while attempts < 30:
        base = random.choice(dict_list)
        synonyms = set()
        for synset in wordnet.synsets(base):
            for lemma in synset.lemmas():
                syn_word = lemma.name().replace('_', '').lower()
                if 4 <= len(syn_word) <= 8 and syn_word.isalpha() and syn_word != base and syn_word in dict_list:
                    synonyms.add(syn_word)
        if len(synonyms) >= 5 and len(synonyms) <= 20:
            return {
                'type': 'synonym',
                'description': f"Word similar to **{base.upper()}**",
                'constraint': base,
                'expected_length': len(base),
                'count': len(synonyms)
            }
        attempts += 1
    return None

def generate_puzzle_type_opposite(dict_list):
    """Words that are opposites of a base word."""
    attempts = 0
    while attempts < 30:
        base = random.choice(dict_list)
        antonyms = set()
        for synset in wordnet.synsets(base):
            for lemma in synset.lemmas():
                for ant in lemma.antonyms():
                    ant_word = ant.name().replace('_', '').lower()
                    if 4 <= len(ant_word) <= 8 and ant_word.isalpha() and ant_word in dict_list:
                        antonyms.add(ant_word)
        if len(antonyms) >= 5 and len(antonyms) <= 20:
            return {
                'type': 'opposite',
                'description': f"Word opposite of **{base.upper()}**",
                'constraint': base,
                'expected_length': len(base),
                'count': len(antonyms)
            }
        attempts += 1
    return None

# Puzzle type weights
puzzle_generators = [
    (generate_puzzle_type_substring, 12),
    (generate_puzzle_type_letters_anywhere, 10),
    (generate_puzzle_type_ends_with, 10),
    (generate_puzzle_type_starts_with, 8),
    (generate_puzzle_type_double_letter, 8),
    (generate_puzzle_type_wordle_block, 12),
    (generate_puzzle_type_rhyme, 12),
    (generate_puzzle_type_jumble, 15),
    (generate_puzzle_type_synonym, 6),
    (generate_puzzle_type_opposite, 6),
]

print("🎲 Generating puzzles...")
puzzles = []
max_attempts_per_type = 50
target_puzzles = 250

attempts = 0
while len(puzzles) < target_puzzles and attempts < 10000:
    attempts += 1
    if attempts % 100 == 0:
        print(f"  Generated {len(puzzles)}/{target_puzzles} puzzles...")
    
    # Weighted random selection
    total_weight = sum(w for _, w in puzzle_generators)
    r = random.uniform(0, total_weight)
    current = 0
    selected_gen = puzzle_generators[0][0]
    
    for gen_func, weight in puzzle_generators:
        current += weight
        if r <= current:
            selected_gen = gen_func
            break
    
    puzzle = selected_gen(dictionary)
    if puzzle:
        puzzles.append(puzzle)

print(f"✅ Generated {len(puzzles)} puzzles")

# Save to file
output_file = "word_rush_puzzles.txt"
with open(output_file, 'w', encoding='utf-8') as f:
    for puzzle in puzzles:
        f.write(json.dumps(puzzle) + '\n')

print(f"💾 Saved to {output_file}")
print(f"📊 Puzzle distribution:")
from collections import Counter
type_counts = Counter(p['type'] for p in puzzles)
for ptype, count in type_counts.most_common():
    print(f"   {ptype}: {count}")

