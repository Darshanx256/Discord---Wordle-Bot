# 🟩 Discord Wordle Bot (V2)

A feature-rich, competitive Wordle bot for Discord, featuring a dual-track progression system (Rating & XP), cosmetic shop, and private solo modes.

## 📂 Project Structure

The project is organized into a modular architecture:

```
Discord---Wordle-Bot/
├── wordle_bot.py       # 🚀 ENTRY POINT
├── src/                # 🧠 CORE LOGIC
│   ├── config.py       # ⚙️ Constants (XP Table, Tiers)
│   ├── bot.py          # 🤖 Main Bot Class & Commands
│   ├── game.py         # 🎮 Game Engine (Solo & Multiplayer)
│   ├── database.py     # 🗄️ Supabase Interaction (V2 RPC)
│   ├── ui.py           # 🎨 Views, Modals, & Embeds
│   ├── server.py       # 🌐 Flask Web Server
│   └── utils.py        # 🛠️ Helpers
├── supabase.txt        # 📜 SQL Schema & Migration Script
├── static/             # 🌐 Web Assets
├── .env                # 🔒 Secrets
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

### 🎒 Features
- **Shop**: Unlock badges like "Duck Lord" or "Dragon Slayer".
- **Collection**: Find rare easter eggs (Ducks, Dragons) randomly in games.
- **Anti-Grind**: Daily soft-caps to encourage consistency over spam.

## 🎮 Commands

- `/help` - Visual guide.
- `/wordle` - Start public simple game.
- `/wordle_classic` - Start public hard game.
- `/solo` - **NEW!** Play privately.
- `/guess [word]` - Submit a guess.
- `/leaderboard` - Server Rankings.
- `/leaderboard_global` - Global Rankings.
- `/profile` - Check your Level, WR, and Collection.
- `/shop` - **NEW!** Equip badges.
- `/stop_game` - Cancel public game.

## 🏆 Ranking Rules

- **XP**: Earned from all games. +50 XP for Win, +10 XP per letter.
- **WR (Rating)**: Based on Wins, Speed (<30s bonus), and Efficiency (fewer guesses).
- **Penalties**: None for Multi. High-rank Solo players risk WR slightly.

## ⚡ Performance

- **Optimized DB**: Logic moved to SQL RPC (`record_game_result_v4`) to minimize latency and ensure data integrity.
- **Concurrency**: Async fetching for large leaderboards.

---
*Created with ❤️ by the Wordle Bot Team.* (ONE MAN)
