
import sys
import os
import asyncio
from unittest.mock import MagicMock, AsyncMock
import discord

# Add current dir to path
sys.path.append(os.getcwd())

async def test_new_game_logic():
    print("Testing New Game logic refactoring...")
    
    # Mock Bot
    bot = MagicMock()
    bot.games = {}
    bot.custom_games = {}
    bot.stopped_games = set()
    bot.secrets = ["apple"]
    bot.hard_secrets = ["organ"]
    bot.guild_classic_tracker = {}
    bot.guild_secret_tracker = {}
    
    # Mock Interaction
    interaction = AsyncMock(spec=discord.Interaction)
    interaction.guild = MagicMock(id=123)
    interaction.channel = MagicMock(id=456)
    interaction.user = MagicMock(id=789, display_name="Tester")
    interaction.response = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    interaction.original_response = AsyncMock(return_value=MagicMock(id=111))
    
    from src.handlers.game_logic import start_multiplayer_game, PlayAgainView
    
    # 1. Test starting a Simple game
    print("\nStarting Simple Game...")
    game = await start_multiplayer_game(bot, interaction, is_classic=False)
    assert game.difficulty == 0
    assert 456 in bot.games
    assert bot.games[456].secret == "apple"
    print("✅ Simple Game started correctly.")
    
    # 2. Test starting a Classic game (should fail if one is active)
    print("\nAttempting Classic Game while Simple is active...")
    # Reset interaction mock for the warning check
    interaction.response.send_message.reset_mock()
    await start_multiplayer_game(bot, interaction, is_classic=True)
    # Check if a warning was sent (the code returns early after sending msg)
    assert interaction.response.send_message.called
    args, kwargs = interaction.response.send_message.call_args
    assert "already active" in args[0]
    print("✅ Correctly blocked starting game when one is active.")
    
    # 3. Test PlayAgainView
    print("\nTesting PlayAgainView...")
    view = PlayAgainView(bot, is_classic=True)
    assert len(view.children) == 1
    assert view.children[0].label == "Play Again"
    print("✅ PlayAgainView initialized correctly.")

    print("\n✅ Verification PASSED!")

if __name__ == "__main__":
    asyncio.run(test_new_game_logic())
