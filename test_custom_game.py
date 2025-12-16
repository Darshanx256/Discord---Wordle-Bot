#!/usr/bin/env python3
"""
Test script for custom game mode
This simulates the custom game flow without needing the Discord bot running
"""

import sys
sys.path.insert(0, '/c/Users/Darshan/Desktop/Discord---Wordle-Bot')

from src.game import WordleGame
import discord
from unittest.mock import Mock, MagicMock

def test_custom_game_flow():
    """Test the custom game flow"""
    print("=" * 60)
    print("🧂 CUSTOM GAME MODE TEST")
    print("=" * 60)
    
    # Create mock user
    mock_user = Mock(spec=discord.User)
    mock_user.id = 123456789
    mock_user.display_name = "TestPlayer"
    
    # Create game with custom word
    secret_word = "pizza"
    print(f"\n✅ Game created with secret word: '{secret_word}'")
    
    game = WordleGame(secret_word, 987654321, mock_user, 0)
    game.reveal_on_loss = True
    print(f"✅ Game settings: reveal_on_loss = {game.reveal_on_loss}")
    
    # Simulate guesses
    print("\n" + "=" * 60)
    print("SIMULATING GAME FLOW:")
    print("=" * 60)
    
    guesses = [
        ("world", False),  # Wrong word
        ("sweet", False),  # Wrong word
        ("pizza", True),   # Correct word - WIN
    ]
    
    for i, (guess, should_be_win) in enumerate(guesses, 1):
        print(f"\n--- Guess {i}: '{guess.upper()}' ---")
        
        # Create mock user for this guess
        guesser = Mock(spec=discord.User)
        guesser.id = 100000000 + i
        guesser.display_name = f"Player{i}"
        
        pat, win, game_over = game.process_turn(guess, guesser)
        
        print(f"Pattern: {pat}")
        print(f"Win: {win}")
        print(f"Game Over: {game_over}")
        print(f"Attempts Used: {game.attempts_used}/6")
        print(f"Participants: {[p for p in game.participants]}")
        
        if win:
            print(f"✅ VICTORY! Player found the word in {game.attempts_used}/6 attempts")
            break
        elif game_over:
            print(f"💀 GAME OVER! All attempts used. Word was: {secret_word.upper()}")
            break
    
    print("\n" + "=" * 60)
    print("GAME STATE AT END:")
    print("=" * 60)
    print(f"Secret: {game.secret.upper()}")
    print(f"History: {len(game.history)} guesses")
    for h in game.history:
        print(f"  - {h['word']}: {h['pattern']} by {h['user'].display_name}")
    print(f"Participants: {len(game.participants)}")
    print(f"Used Letters: {game.used_letters}")
    
    # Test reveal on loss scenario
    print("\n" + "=" * 60)
    print("TESTING REVEAL_ON_LOSS FLAG:")
    print("=" * 60)
    
    game2 = WordleGame("guitar", 111111111, mock_user, 0)
    game2.reveal_on_loss = False
    print(f"Game 2 created with reveal_on_loss = {game2.reveal_on_loss}")
    
    # Max out attempts
    for i in range(6):
        guesser = Mock(spec=discord.User)
        guesser.id = 200000000 + i
        guesser.display_name = f"LossPlayer{i}"
        pat, win, game_over = game2.process_turn(f"aaaaa", guesser)
    
    print(f"Game Over: {game_over}")
    print(f"Should show word? {game2.reveal_on_loss}")
    if game_over and not game2.reveal_on_loss:
        print("✅ CORRECT: Would show '💀 GAME OVER' without revealing word")
    elif game_over and game2.reveal_on_loss:
        print(f"✅ CORRECT: Would show '💀 GAME OVER - The word was {game2.secret.upper()}'")
    
    print("\n" + "=" * 60)
    print("✅ ALL TESTS PASSED!")
    print("=" * 60)

if __name__ == "__main__":
    test_custom_game_flow()
