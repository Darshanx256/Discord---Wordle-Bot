# Tier Admin Bot

Discord bot focused on:
- Hourly tier-role sync from leaderboard data (`user_stats_v2.multi_wr`)
- Private Flask admin panel served with Waitress (WSGI)
- Admin-only tools for relay messaging, role management, and badge updates

## Run

```bash
pip install -r requirements.txt
python tier_admin_bot.py
```

## Required Env

- `TIER_BOT_TOKEN` (or `DISCORD_TOKEN` fallback)
- `TIER_BOT_GUILD_ID`
- `SUPABASE_URL`
- `SUPABASE_KEY`

## Recommended Env

- `TIER_BOT_APP_SECRET`
- `PORT` (default `8080`)
- `TIER_BOT_SYNC_INTERVAL_SECONDS` (default `3600`)
- `TIER_BOT_MEMBER_UPDATE_DELAY_SECONDS` (default `1.0`)
- `TIER_ROLE_PREFIX` (default `Tier`)
- `SESSION_COOKIE_SECURE=1` when behind HTTPS

## Files Kept

- `tier_admin_bot.py`
- `src/tier_admin_bot.py`
- `src/config.py`
- `src/__init__.py`
- `requirements.txt`
