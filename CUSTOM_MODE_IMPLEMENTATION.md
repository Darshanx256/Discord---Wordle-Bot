# Custom Game Mode Feature - Implementation Summary

## Overview
Added a new `/custom` command that allows users to create temporary custom Wordle games with their own 5-letter words. These games are fun, give no XP/WR rewards, and are perfect for casual gameplay.

## Files Modified

### 1. **src/bot.py**
- Added `self.custom_games = {}` dictionary to store active custom games
- Updated `cleanup_task()` to clean up idle custom games (24 hours)

### 2. **src/cogs/game_commands.py**
- Added `CustomWordModal` class: Modal for entering custom word and reveal settings
- Added `CustomSetupView` class: UI buttons (Set Up, Cancel) for setup
- Added `/custom` hybrid command: Triggers ephemeral setup message
- **Features:**
  - Validates input: must be 5 letters, alpha only
  - Validates reveal option: "yes" or "no"
  - Prevents multiple custom games in one channel
  - Adds custom word to bot's valid_set temporarily
  - Shows announcement in channel when game starts

### 3. **src/game.py**
- Added `reveal_on_loss` to `__slots__` in WordleGame class
- Initialize `reveal_on_loss = True` by default
- Allows custom games to control word reveal on loss

### 4. **src/cogs/guess_handler.py**
- Modified `/guess` command to detect and handle custom games
- Added check for `custom_game` in addition to regular `game`
- **Custom Game Logic:**
  - On win: Shows victory message without DB recording
  - On loss: Shows game over message, optionally reveals word based on setting
  - During turn: Shows current attempt without stats
  - Exits without DB recording (no XP, WR, or stats)

### 5. **src/handlers/game_logic.py**
- Fixed level up notifications (previous issue):
  - Collect level ups from all participants, not just winner
  - Return level_ups list from both handle_game_win() and handle_game_loss()
  - Send notifications for each player who levels up

## How the Feature Works

### Step 1: User starts custom game
```
/custom
```

### Step 2: Bot sends ephemeral setup message
```
🧂 CUSTOM MODE
Set up a game in *this* chat with your own custom word
How it works?
• Click Set Up and enter a 5-letter word
• A wordle match would start, others can use /guess
• This mode gives no XP or WR score

[Set Up] [Cancel]
```

### Step 3: User clicks "Set Up" and fills modal
- **Field 1:** Enter 5-letter word (e.g., PIZZA)
- **Field 2:** Reveal word on loss? (yes/no)

### Step 4: Game setup and announcement
- Word added to valid_set temporarily
- Game stored in bot.custom_games[channel_id]
- Channel announcement: "🧂 Custom Wordle Game Started by [username]"

### Step 5: Gameplay
- Players use `/guess word:xxxxx` to make guesses
- Displays board state without XP/WR
- No database recording

### Step 6: Game ends
- **Win:** "🏆 VICTORY! [Player] found [WORD] in X/6!"
- **Loss with reveal:** "💀 GAME OVER - The word was [WORD]"
- **Loss without reveal:** "💀 GAME OVER - Better luck next time!"

## Validation & Error Handling

✅ **Input Validation:**
- Word must be exactly 5 letters
- Must contain only alphabetic characters
- Case-insensitive (PIZZA, pizza, Pizza all valid)
- No empty inputs

✅ **Game State Checks:**
- Prevents multiple custom games in one channel
- Prevents starting custom game if regular game is active
- Prevents starting game if custom game already exists

✅ **Error Messages:**
- Invalid word length: "❌ Invalid input! Word must be exactly 5 letters"
- Non-alpha characters: "❌ Invalid input! Word must be exactly 5 letters (alphabetic only)"
- Invalid reveal option: "❌ Reveal must be 'yes' or 'no'."
- Game already active: "⚠️ A custom game is already active in this channel!"

## Database & Rewards

✅ **Custom games do NOT:**
- Record to database
- Award XP points
- Affect WR (Wordle Rating)
- Trigger achievements
- Track statistics
- Collect eggs

## Cleanup

- Games clean up automatically on win/loss
- Idle games (24+ hours) cleaned up by `cleanup_task()`
- Custom word removed from valid_set when game completes
- No memory leaks

## Testing

All core functionality tested:
- ✅ Game creation with custom words
- ✅ Win/loss detection
- ✅ Reveal toggle functionality
- ✅ Input validation
- ✅ Multiple players interaction
- ✅ No database recording
- ✅ Proper cleanup

## Branch
Ready to merge to BETA branch
