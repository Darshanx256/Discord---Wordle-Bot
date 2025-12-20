
import sys
import os
from unittest.mock import MagicMock

# Add current dir to path
sys.path.append(os.getcwd())

class MockMessage:
    def __init__(self, content, channel_id, author_id):
        self.content = content
        self.channel = MagicMock(id=channel_id)
        self.author = MagicMock(id=author_id)

def test_refined_prefix_logic():
    print("Testing refined custom prefix logic...")
    
    from src.bot import WordleBot
    bot = WordleBot()
    
    cid_active = 100
    cid_inactive = 200
    uid_active = 1000
    uid_inactive = 2000
    
    # Simulate active games
    bot.games[cid_active] = MagicMock()
    bot.solo_games[uid_active] = MagicMock()
    
    test_cases = [
        # (content, cid, uid, expected)
        ("-g apple", cid_active, uid_inactive, "-"),    # Active channel game
        ("-g pizza", cid_inactive, uid_active, "-"),     # Active solo game
        ("-g organ", cid_inactive, uid_inactive, []),  # No game active
        ("-G APPLE", cid_active, uid_inactive, "-"),    # Case insensitivity check
        ("!wordle", cid_active, uid_inactive, []),      # Other prefix check
    ]
    
    for content, cid, uid, expected in test_cases:
        msg = MockMessage(content, cid, uid)
        prefix = bot.get_custom_prefix(bot, msg)
        print(f"Content: '{content}' (CID:{cid}, UID:{uid}) -> Prefix: {prefix} (Expected: {expected})")
        assert prefix == expected

    print("\n✅ Verification PASSED!")

if __name__ == "__main__":
    test_refined_prefix_logic()
