import nltk
import os

# Ensure nltk data is available
try:
    from nltk.corpus import words
    word_set = set(w.lower() for w in words.words())
except:
    nltk.download('words')
    from nltk.corpus import words
    word_set = set(w.lower() for w in words.words())

def is_plural(word):
    word = word.lower()
    if not word.endswith('s'):
        return False
    
    # Exceptions: Common 5-letter singular words ending in 's'
    # This is a heuristic list, ideally we'd use a better lemmatizer but this is fast.
    exceptions = {
        'basis', 'oasis', 'glass', 'cross', 'dress', 'bliss', 'chess', 'chaos', 
        'bonus', 'focus', 'minus', 'virus', 'guess', 'press', 'grass', 'alias',
        'brass', 'class', 'gloss', 'gross', 'abyss', 'atlas', 'ethos', 'logos',
        'idols', 'jeans', 'news', 'tongs', 'pants', 'series', 'species', 'axis',
        'thesis', 'mess', 'less', 'bless', 'floss', 'moss', 'ross', 'loss'
    }
    if word in exceptions:
        return False
    
    # Heuristic: ends in 'ss' -> usually singular
    if word.endswith('ss'):
        return False
        
    # Heuristic: Ends in 's', and removing 's' results in a valid 4-letter word
    if word.endswith('s'):
        stem = word[:-1]
        if stem in word_set:
            return True
            
    # Ends in 'es', removing 'es' results in a valid word (e.g., boxes -> box)
    if word.endswith('es'):
        stem = word[:-2]
        if stem in word_set:
            return True
        # Try removing just 's' if stem not found (e.g., horses -> horse)
        stem = word[:-1]
        if stem in word_set:
            return True
            
    return False

def filter_file(input_path, output_path):
    with open(input_path, 'r', encoding='utf-8') as f:
        all_5_words = [line.strip().lower() for line in f if len(line.strip()) == 5]
    
    singulars = []
    plurals = []
    
    for word in all_5_words:
        if is_plural(word):
            plurals.append(word)
        else:
            singulars.append(word)
            
    print(f"Total 5-letter words: {len(all_5_words)}")
    print(f"Singulars found: {len(singulars)}")
    print(f"Plurals filtered: {len(plurals)}")
    
    with open(output_path, 'w', encoding='utf-8') as f:
        for word in sorted(singulars):
            f.write(f"{word}\n")
    
    # Optional: Log plurals for verification
    with open('plurals_filtered.txt', 'w', encoding='utf-8') as f:
        for word in sorted(plurals):
            f.write(f"{word}\n")

if __name__ == "__main__":
    filter_file('all_words.txt', 'singular_5.txt')
