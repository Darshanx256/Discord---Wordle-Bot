from src.mechanics.constraint_logic import ConstraintGenerator
import os

# Mock dictionary
mock_dict = ["apple", "bread", "clear", "dance", "eagle", "fruit", "grape", "house", "image", "joker", "knack", "lemon", "melon", "night", "ocean", "patch", "queen", "river", "snake", "train", "under", "voice", "water", "xenon", "yacht", "zebra"]

# Load real dictionary if possible
real_dict_path = "all_words.txt"
if os.path.exists(real_dict_path):
    with open(real_dict_path, "r") as f:
        dictionary = [w.strip().lower() for w in f if len(w.strip()) == 5]
else:
    dictionary = mock_dict

gen = ConstraintGenerator(dictionary)

print(f"Loaded dictionary with {len(dictionary)} words.")

success_count = 0
total_tests = 10
stats = {}

for i in range(total_tests):
    puzzle = gen.generate_puzzle()
    if puzzle:
        success_count += 1
        desc = puzzle['description']
        # Extract type from desc (hacky)
        dtype = desc.split("**")[0] if "**" in desc else desc
        stats[dtype] = stats.get(dtype, 0) + 1
        
        if i < 5:
            print(f"Test {i+1}: {desc} | Solutions: {len(puzzle['solutions'])}")

print(f"\nSuccess Rate: {success_count}/{total_tests}")
print("\nStats by Type Prefix:")
for k, v in stats.items():
    print(f"- {k}: {v}")
