# 🟩 Discord Wordle Bot

A feature-rich, competitive Wordle bot for Discord, complete with a global leaderboard, competitive ranking system (Elo-like), and a web dashboard status page.

## 📂 Project Structure

The project is organized into a modular architecture for clarity and maintainability.

```
Discord---Wordle-Bot/
├── wordle_bot.py       # 🚀 ENTRY POINT: Run this to start the bot.
├── src/                # 🧠 CORE LOGIC
│   ├── config.py       # ⚙️ Configuration constants & Environment variables
│   ├── bot.py          # 🤖 Main Bot Class & Command Definitions
│   ├── game.py         # 🎮 Game Logic (Wordle Engine)
│   ├── database.py     # 🗄️ Supabase Interaction Layer
│   ├── ui.py           # 🎨 Discord UI Views & Formatting
│   ├── server.py       # 🌐 Flask Web Server (Status Page)
│   └── utils.py        # 🛠️ Helper Functions & Emoji Loading
├── static/             # 🌐 Web Assets (HTML/CSS) for the Flask server
├── .env                # 🔒 Secrets (Token, DB Keys) - DO NOT SHARE
└── requirements.txt    # 📦 Dependencies
```

## 🧩 Module Guide

### `src/bot.py`
The heart of the application. It initializes the `discord.py` bot, sets up slash commands (`/wordle`, `/guess`, `/profile`), and handles the startup sequence (`setup_hook`).

### `src/game.py`
Contains the `WordleGame` class. This handles the core mechanics: checking guesses, coloring letters (🟩🟨⬜), managing turn history, and detecting win/loss conditions.

### `src/database.py`
Manages all data persistence using **Supabase**.
- `update_leaderboard`: Upserts scores after games.
- `get_next_secret`: Fetches non-repeated words for guilds.
- `fetch_profile_stats_sync`: Aggregates complex user stats (Rank, Tier, Percentile).

### `src/ui.py`
Handles visual elements.
- `LeaderboardView`: The interactive pagination buttons for leaderboards.
- `get_markdown_keypad_status`: Generates the dynamic keyboard visualization.

### `src/server.py`
A lightweight **Flask** server running in a separate thread. It serves static pages (`/`, `/terms`, `/privacy`) required for Discord App Verification and status monitoring.

## 🚀 How to Run

1.  **Install Dependencies**
    ```bash
    pip install -r requirements.txt
    ```

2.  **Configure Environment**
    Ensure your `.env` file has the following:
    ```env
    DISCORD_TOKEN=your_token
    APP_ID=your_app_id
    SUPABASE_URL=your_db_url
    SUPABASE_KEY=your_db_key
    ```

3.  **Start the Bot**
    ```bash
    python wordle_bot.py
    ```

## 🎮 Commands

- `/help` - View the interactive guide (How to play, Ranking info).
- `/wordle` - Start a classic 5-letter game (Simple dictionary).
- `/wordle_classic` - Start a harder game (Full dictionary).
- `/guess [word]` - Submit a guess.
- `/board` - View the current board status.
- `/leaderboard` - View the server leaderboard.
- `/leaderboard_global` - View the global cross-server leaderboard.
- `/profile` - View your detailed stats and rank.
- `/stop_game` - Cancel the current game.

## 🏆 Ranking System

The bot uses a **Bayesian Average** system for ranking to ensure fairness.
- **Grandmaster** 💎 (Top 10%)
- **Master** ⚜️ (Top 35%)
- **Elite** ⚔️ (Top 60%)
- **Challenger** 🛡️ (Remainder)

## ⚡ Performance

- **Waitress WSGI**: Production-grade server for stability.
- **Async Optimization**: Parallel execution for leaderboard fetching to handle scale.
- **Supabase**: Persistent, relational data storage.

---
*Created with ❤️ by the Wordle Bot Team. (One man)*
