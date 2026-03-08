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
- **Discord Integration UI**: Open a live browser board from the in-chat game buttons (Flask + Waitress).

## 🌐 Discord Integration UI (Flask-SocketIO + gevent)

The bot now starts a lightweight web server for in-browser gameplay integration with live Socket.IO updates.

### What it does
- Adds an **Open Integration UI** button next to the modal guess button.
- Opens a signed link tied to your Discord user and active game.
- Mirrors channel/custom integration guesses back into Discord chat via existing `/guess` flow.
- Supports Solo sessions privately from the same integration endpoint.

### Environment variables
- `INTEGRATION_BASE_URL` (default: `http://127.0.0.1:8787`)
- `INTEGRATION_HOST` (default: `0.0.0.0`)
- `INTEGRATION_PORT` (default: `8787`)
- `INTEGRATION_TOKEN_SECRET` (recommended in production)
- `INTEGRATION_WS_CORS_ORIGINS` (comma-separated list of allowed origins for Socket.IO; defaults to the origin of `INTEGRATION_BASE_URL`)
- `INTEGRATION_ASYNC_MODE` (optional override: `gevent` or `threading`)

### Run locally
1. Install deps: `pip install -r requirements.txt`
2. Set your Discord/Supabase env vars as usual.
3. Optional: set `INTEGRATION_BASE_URL` to your tunnel/public URL if remote users must access it.
4. Start bot normally (`python wordle_bot.py` or your existing entry command).
5. In Discord, start a game and press **Open Integration UI**.

### Important deploy note
- If users outside your machine should access the UI, `INTEGRATION_BASE_URL` must point to a reachable public domain or tunnel URL that routes to `INTEGRATION_HOST:INTEGRATION_PORT`.
- Replace `src/discord_integrations/static/logo-placeholder.svg` with your own logo file when ready.
- Socket.IO requires a WebSocket-capable server. Install `gevent` (included in requirements) for production.

## 🎯 Discord Activity Mode (Embedded App)

The same web UI now supports an Activity bootstrap path at:
- `/integration/activity`

This keeps gameplay logic unchanged and only changes how session/auth is established.

### Required environment variables (Activity)
- `DISCORD_ACTIVITY_CLIENT_ID` (or `APP_ID`)
- `DISCORD_CLIENT_SECRET`
- `INTEGRATION_BASE_URL` must be public HTTPS (required by Discord OAuth redirect rules)

### Backend endpoints used by Activity bootstrap
- `POST /integration/api/activity/oauth-token` (OAuth code -> access token)
- `POST /integration/api/activity/session-token` (Discord user token -> signed Wordle session token)

### Developer Portal checklist
1. Enable the app for Activities/Embedded usage in Discord Developer Portal.
2. Add redirect URI: `<YOUR_PUBLIC_BASE_URL>/integration/activity`
3. Ensure your app is installed in the test server.
4. Launch Activity in Discord, then the web client bootstraps and binds to channel game state.

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
