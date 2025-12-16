#!/usr/bin/env python3
"""Quick diagnostic to import and test cogs."""
import sys
import traceback

print("=" * 60)
print("COG IMPORT DIAGNOSTIC")
print("=" * 60)

cogs = [
    "src.cogs.game_commands",
    "src.cogs.guess_handler",
    "src.cogs.profile_commands",
    "src.cogs.leaderboard",
    "src.cogs.help_commands",
]

for cog_path in cogs:
    print(f"\n📦 Importing {cog_path}...")
    try:
        mod = __import__(cog_path, fromlist=[cog_path.split(".")[-1]])
        print(f"   ✅ Success")
        # Check for setup function
        if hasattr(mod, 'setup'):
            print(f"   ✅ Has 'setup' function")
        else:
            print(f"   ❌ Missing 'setup' function!")
    except Exception as e:
        print(f"   ❌ FAILED:")
        traceback.print_exc()

print("\n" + "=" * 60)
print("DONE")
print("=" * 60)
