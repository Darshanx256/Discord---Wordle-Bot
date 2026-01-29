# 🟩 Discord Wordle Game Bot (V4)

A feature-rich, competitive Wordle bot for Discord, featuring a dual-track progression system (Rating & XP), cosmetic shop, and private solo modes.

## 📂 Project Structure

The project is organized into a modular architecture:

```
Discord---Wordle-Bot/
├── wordle_bot.py       # 🚀 ENTRY POINT
├── src/                # 🧠 CORE LOGIC
│   ├── bot.py          # 🤖 Bot Initialization & Background Tasks
│   ├── config.py       # ⚙️ Constants & Global Configuration
│   ├── database.py     # 🗄️ Supabase Client & DB Handlers
│   ├── game.py         # 🎮 Game Data Models
│   ├── ui.py           # 🎨 Shared Views, Modals, & Embeds
│   ├── utils.py        # 🛠️ Helper Functions & Emojis
│   ├── cogs/           # 🧩 Discord Command Modules (Cogs)
│   │   ├── game_commands.py   # Main Game Flow
│   │   ├── guess_handler.py   # Guess & Win/Loss logic
│   │   ├── constraint_mode.py # Rush Mode (⚡)
│   │   └── race_commands.py   # Sync Race Logic
│   ├── mechanics/      # ⚙️ Game Rules & Mechanics
│   │   ├── streaks.py         # Streak management
│   │   ├── rewards.py         # XP & Rating formulas
│   │   └── constraint_logic.py# Rush Mode generation
│   └── handlers/       # 🏁 Win/Loss Processing
│       └── game_logic.py      # Reward distribution
├── static/             # 🌐 Web Assets (Landing Page)
├── supabase.txt        # 📜 SQL Schema & Migration Script
└── requirements.txt    # 📦 Dependencies
```

## 🧩 New Features

### 📈 Progression System
- **Wordle Rating (WR)**: Skill-based ladder (Separate Solo vs Multiplayer).
- **Player Level (XP)**: Activity-based progression. Never decreases.
- **Tiers**: Challenger 🛡️ -> Elite ⚔️ -> Master ⚜️ -> Grandmaster 💎.

### 🎮 Game Modes
- **Multiplayer**: Coop/Competitive in a channel (`/wordle`).
- **Solo**: Private, ephemeral game using Discord Buttons & Modals (`/solo`).
- **Classic**: Hard mode with full dictionary (`/wordle_classic`).
- **Race**: **NEW!** Competitive race mode - everyone solves the same word (`/race`).
- **Custom**: Setup games with custom words and extensive options (`/custom`).

### 🎒 Features
- **Shop**: Unlock badges like "Duck Lord" or "Dragon Slayer".
- **Collection**: Find rare easter eggs (Ducks, Dragons) randomly in games.
- **Anti-Grind**: Daily soft-caps to encourage consistency over spam.

## 🎮 Commands

- `/help` - Visual guide.
- `/wordle` - Start public simple game.
- `/wordle_classic` - Start public hard game.
- `/solo` - Play privately.
- `/race` - **NEW!** Start competitive race lobby.
- `/showrace` - **NEW!** Recover your race game.
- `/word_rush` - **NEW!** Fast paced, constraint based word game.
- `/hard_mode` - **NEW!** Wordle with Official Hard-Mode rules
- `/custom` - Start custom game with extensive options.
- `/guess [word]` - Submit a guess.
- `/leaderboard` - Server Rankings.
- `/leaderboard_global` - Global Rankings.
- `/profile` - Check your Level, WR, and Collection.
- `/shop` - Equip badges.
- `/stop_game` - Cancel public game.

## 🏆 Ranking Rules

- **XP**: Earned from all games. +50 XP for Win, +10 XP per letter.
- **WR (Rating)**: Based on Wins, Speed (<60s bonus), and Efficiency (fewer guesses).

## ⚡ Performance

- **Optimized DB**: Logic moved to SQL RPC (`record_game_result_v4`) to minimize latency and ensure data integrity.
- **Concurrency**: Async fetching for large leaderboards.
- **Scalability**: Per-user state optimization, API batching, and TTL caching.

## 📊 Telemetry & Tracking

To gain insights into game activity and player behavior, the bot implements a flexible event tracking system.

- **Storage**: `event_logs_v1` table with a `JSONB` metadata column.
- **Function**: `log_event_v1(bot, event_type, user_id, guild_id, metadata)` in `src/database.py`.
- **Flexibility**: New events or additional data points can be tracked without database schema changes.
- **Fail-Safe**: Tracking logic is designed to fail silently, ensuring game stability is never compromised by telemetry errors.

### Tracked Events
- `word_rush_checkpoint`: Logs performance at every checkpoint.
- `word_rush_complete`: Logs final results and session MVPs.

## 🛡️ Development Standards

To maintain production-grade stability and scalability, all new features MUST follow these standards:

1.  **Event-Driven Over Polling**: Use `asyncio.Task` with dynamic sleeps for timers instead of global polling loops.
2.  **Monotonic Timing**: Always use `time.monotonic()` for intervals and timeouts to avoid system clock drift.
3.  **Memory Efficiency**: Use `__slots__` in all core game and session classes to minimize RAM footprint.
4.  **API Batching**: Never perform $N$ database calls in a loop. Use batched fetching (e.g., `.in_('id', ids)`) for multi-user operations.
5.  **State Management**: Keep per-user state minimal and use ephemeral storage where possible.
6.  **Cached Validation**: Cache frequent external data lookups (e.g., user profiles) with appropriate TTL.

---
*Created with ❤️ by the Wordle Game Bot Team.* (ONE MAN)
