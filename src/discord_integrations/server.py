import asyncio
import base64
import hashlib
import hmac
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote

import requests

from src.database import fetch_user_profile_v2


_SERVER_LOCK = threading.Lock()
_SERVER_THREAD = None
_SERVER_STARTED = False
_FINISHED_STATE_CACHE: Dict[str, Dict[str, Any]] = {}
_FINISHED_STATE_TTL_SECONDS = 7200
_DISCORD_USER_CACHE: Dict[str, Dict[str, Any]] = {}
_DISCORD_USER_CACHE_TTL_SECONDS = 1800
_INTEGRATION_USER_CACHE: Dict[int, Dict[str, Any]] = {}
_INTEGRATION_USER_CACHE_TTL_SECONDS = 86400
_INTEGRATION_USER_CACHE_MAX = 1024


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(raw: str) -> bytes:
    padding = "=" * ((4 - len(raw) % 4) % 4)
    return base64.urlsafe_b64decode(raw + padding)


def _token_secret() -> str:
    return (
        os.getenv("INTEGRATION_TOKEN_SECRET")
        or os.getenv("DISCORD_TOKEN")
        or "local-dev-integration-secret"
    )


def _sign_token(payload: Dict[str, Any]) -> str:
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_b64 = _b64url_encode(payload_json)
    sig = hmac.new(_token_secret().encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).digest()
    return f"{payload_b64}.{_b64url_encode(sig)}"


def _verify_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        expected = hmac.new(_token_secret().encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).digest()
        provided = _b64url_decode(sig_b64)
        if not hmac.compare_digest(expected, provided):
            return None
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


def _mode_label(game, scope: str) -> str:
    if scope == "solo":
        return "Solo"
    if getattr(game, "difficulty", None) == 2:
        return "Custom"
    if getattr(game, "hard_mode", False):
        return "Hard"
    if getattr(game, "custom_dict", None) is not None:
        return "Custom"
    return "Classic"


def _discord_api_base() -> str:
    return os.getenv("DISCORD_API_BASE", "https://discord.com/api/v10").rstrip("/")


def _activity_client_id() -> str:
    return os.getenv("DISCORD_ACTIVITY_CLIENT_ID") or os.getenv("APP_ID") or ""


def _activity_client_secret() -> str:
    return os.getenv("DISCORD_CLIENT_SECRET") or os.getenv("ACTIVITY_CLIENT_SECRET") or ""


def _cache_discord_user(access_token: str, user_payload: Dict[str, Any]) -> None:
    _DISCORD_USER_CACHE[access_token] = {"payload": user_payload, "stored_at": time.time()}


def _cache_known_integration_user(uid: int, user_payload: Dict[str, Any]) -> None:
    if uid <= 0:
        return

    now_ts = time.time()
    stale = [k for k, v in _INTEGRATION_USER_CACHE.items() if now_ts - float(v.get("stored_at", 0)) > _INTEGRATION_USER_CACHE_TTL_SECONDS]
    for k in stale:
        _INTEGRATION_USER_CACHE.pop(k, None)

    name = (
        str(user_payload.get("name") or "").strip()
        or str(user_payload.get("global_name") or "").strip()
        or str(user_payload.get("username") or "").strip()
        or str(uid)
    )
    avatar_url = str(user_payload.get("avatar_url") or "").strip()
    if not avatar_url:
        avatar_hash = str(user_payload.get("avatar") or "").strip()
        if avatar_hash:
            avatar_url = f"https://cdn.discordapp.com/avatars/{uid}/{avatar_hash}.png?size=128"

    _INTEGRATION_USER_CACHE[uid] = {"name": name, "avatar_url": avatar_url, "stored_at": now_ts}

    if len(_INTEGRATION_USER_CACHE) > _INTEGRATION_USER_CACHE_MAX:
        oldest_uid = min(_INTEGRATION_USER_CACHE, key=lambda k: float(_INTEGRATION_USER_CACHE[k].get("stored_at", 0)))
        _INTEGRATION_USER_CACHE.pop(oldest_uid, None)


def _get_known_integration_user(uid: int) -> Optional[Dict[str, Any]]:
    item = _INTEGRATION_USER_CACHE.get(uid)
    if not item:
        return None
    if time.time() - float(item.get("stored_at", 0)) > _INTEGRATION_USER_CACHE_TTL_SECONDS:
        _INTEGRATION_USER_CACHE.pop(uid, None)
        return None
    return item


def _get_cached_discord_user(access_token: str) -> Optional[Dict[str, Any]]:
    item = _DISCORD_USER_CACHE.get(access_token)
    if not item:
        return None
    if time.time() - float(item.get("stored_at", 0)) > _DISCORD_USER_CACHE_TTL_SECONDS:
        _DISCORD_USER_CACHE.pop(access_token, None)
        return None
    return item.get("payload")


def _fetch_discord_user(access_token: str) -> Dict[str, Any]:
    cached = _get_cached_discord_user(access_token)
    if cached:
        return cached

    res = requests.get(
        f"{_discord_api_base()}/users/@me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    if res.status_code >= 400:
        raise RuntimeError(f"Discord auth rejected access token ({res.status_code}).")
    payload = res.json()
    _cache_discord_user(access_token, payload)
    return payload


def _exchange_activity_oauth_code(code: str, redirect_uri: str) -> Dict[str, Any]:
    client_id = _activity_client_id()
    client_secret = _activity_client_secret()
    if not client_id or not client_secret:
        raise RuntimeError("Missing DISCORD_ACTIVITY_CLIENT_ID/APP_ID or DISCORD_CLIENT_SECRET.")

    res = requests.post(
        f"{_discord_api_base()}/oauth2/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=12,
    )
    if res.status_code >= 400:
        raise RuntimeError(f"Discord OAuth exchange failed ({res.status_code}): {res.text[:160]}")
    return res.json()


def _session_cache_key(payload: Dict[str, Any]) -> str:
    scope = payload.get("scope", "channel")
    uid = int(payload.get("uid", 0))
    cid = int(payload.get("cid", 0))
    # Channel finished-state cache should be shared across participants in the same channel.
    # Solo cache remains user-specific.
    if scope == "channel":
        return f"channel:{cid}"
    return f"{scope}:{uid}:{cid}"


def _cleanup_finished_cache() -> None:
    now_ts = time.time()
    stale_keys = []
    for key, value in _FINISHED_STATE_CACHE.items():
        if now_ts - float(value.get("stored_at", 0)) > _FINISHED_STATE_TTL_SECONDS:
            stale_keys.append(key)
    for key in stale_keys:
        _FINISHED_STATE_CACHE.pop(key, None)


def _cache_finished_state(payload: Dict[str, Any], state: Dict[str, Any], retry_meta: Optional[Dict[str, Any]]) -> None:
    _cleanup_finished_cache()
    key = _session_cache_key(payload)
    _FINISHED_STATE_CACHE[key] = {
        "state": state,
        "retry_meta": retry_meta or {},
        "stored_at": time.time(),
    }


def _load_finished_state(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    _cleanup_finished_cache()
    key = _session_cache_key(payload)
    return _FINISHED_STATE_CACHE.get(key)


def cache_integration_finished_channel_state(bot, game, channel_id: int, actor_user_id: int = 0) -> None:
    """
    Public helper for non-web game flows (e.g. chat guesses) to preserve a final
    integration snapshot so web participants can still see end state and retry UI.
    Fetches full profile for accurate end-state data.
    """
    try:
        cid = int(channel_id or 0)
        if cid <= 0:
            return
        uid = int(actor_user_id or 0)
        state = _snapshot_from_game(bot, game, "channel", uid, skip_profile_fetch=False)
        retry_meta = {
            "scope": "channel",
            "is_classic": bool(getattr(game, "difficulty", 0) == 1),
            "hard_mode": bool(getattr(game, "hard_mode", False)),
            "is_custom": bool(getattr(game, "difficulty", 0) == 2),
            "cid": cid,
            "uid": uid,
        }
        payload = {"uid": uid, "cid": cid, "scope": "channel", "exp": int(time.time()) + 7200}
        _cache_finished_state(payload, state, retry_meta)
    except Exception:
        pass


def _build_breakdown(game) -> list[Dict[str, Any]]:
    stats: Dict[int, Dict[str, Any]] = {}
    secret = str(game.secret).upper()
    for entry in getattr(game, "history", []):
        user = entry.get("user")
        user_payload = _row_user_payload(user)
        uid = int(user_payload["id"])
        guess = (entry.get("word") or "").upper()
        greens = sum(1 for idx in range(min(5, len(guess), len(secret))) if guess[idx] == secret[idx])
        row = stats.setdefault(uid, {
            "name": user_payload["name"],
            "avatar_url": user_payload["avatar_url"],
            "attempts": 0,
            "best_greens": 0,
            "solved": False,
        })
        row["attempts"] += 1
        if greens > row["best_greens"]:
            row["best_greens"] = greens
        if guess == secret:
            row["solved"] = True

    ranking = sorted(
        stats.values(),
        key=lambda item: (
            0 if item["solved"] else 1,
            -item["best_greens"],
            item["attempts"],
            item["name"].lower(),
        ),
    )
    output = []
    for idx, item in enumerate(ranking, start=1):
        output.append({
            "rank": idx,
            "name": item["name"],
            "avatar_url": item["avatar_url"],
            "attempts": item["attempts"],
            "best_greens": item["best_greens"],
            "solved": item["solved"],
        })
    return output


def _evaluate_guess(guess: str, secret: str) -> list[str]:
    guess = guess.upper()
    secret = secret.upper()
    result = ["absent"] * 5
    s_chars = list(secret)
    g_chars = list(guess)

    for idx in range(5):
        if g_chars[idx] == s_chars[idx]:
            result[idx] = "correct"
            s_chars[idx] = None
            g_chars[idx] = None

    for idx in range(5):
        if result[idx] == "correct":
            continue
        ch = g_chars[idx]
        if ch is not None and ch in s_chars:
            result[idx] = "present"
            s_chars[s_chars.index(ch)] = None

    return result


def _row_user_payload(user_obj) -> Dict[str, Any]:
    if user_obj is None:
        return {"name": "Unknown", "avatar_url": "", "id": 0}
    try:
        avatar_url = str(user_obj.display_avatar.url)
    except Exception:
        avatar_url = ""
    uid = int(getattr(user_obj, "id", 0) or 0)
    name = getattr(user_obj, "display_name", str(getattr(user_obj, "id", "Unknown")))
    payload = {"name": name, "avatar_url": avatar_url, "id": uid}
    _cache_known_integration_user(uid, payload)
    return payload


async def _background_cache_profile(bot, user_id: int) -> None:
    """Fetch and cache user profile in background while animations play (~550ms)."""
    try:
        # Fetch with cache=True to avoid re-fetching if already cached
        from src.database import fetch_user_profile_v2
        fetch_user_profile_v2(bot, user_id, use_cache=True)
    except Exception:
        # Silently fail - this is background work, don't block anything
        pass


def _snapshot_from_game(bot, game, scope: str, owner_user_id: int, skip_profile_fetch: bool = False) -> Dict[str, Any]:
    history_rows = []
    winner = None
    for item in getattr(game, "history", []):
        word = (item.get("word") or "").upper()
        states = _evaluate_guess(word, game.secret)
        user_payload = _row_user_payload(item.get("user"))
        row = {"word": word, "states": states, "user": user_payload}
        history_rows.append(row)
        if word == str(game.secret).upper():
            winner = user_payload

    participants = len(getattr(game, "participants", set()) or set())
    if scope == "solo":
        participants = 1

    # Skip profile fetch during active gameplay for faster response times
    # Only fetch when specifically needed (e.g., retry/end state)
    wr_value = "—"
    if not skip_profile_fetch:
        try:
            profile = fetch_user_profile_v2(bot, owner_user_id, use_cache=True)
            if profile:
                wr_key = "solo_wr" if scope == "solo" else "multi_wr"
                wr_value = profile.get(wr_key, "—")
        except Exception:
            wr_value = "—"

    mode = _mode_label(game, scope)
    return {
        "mode_label": mode,
        "wr": wr_value,
        "participants": participants,
        "attempts_used": int(getattr(game, "attempts_used", 0)),
        "max_attempts": int(getattr(game, "max_attempts", 6)),
        "rows": history_rows,
        "game_over": winner is not None or int(getattr(game, "attempts_used", 0)) >= int(getattr(game, "max_attempts", 6)),
        "winner": winner,
        "secret": str(game.secret).upper() if winner is not None or int(getattr(game, "attempts_used", 0)) >= int(getattr(game, "max_attempts", 6)) else "",
        "breakdown": _build_breakdown(game),
        "can_retry": bool(mode in {"Classic", "Hard", "Solo"}),
    }


async def _load_snapshot(bot, payload: Dict[str, Any]) -> Dict[str, Any]:
    uid = int(payload["uid"])
    scope = payload.get("scope", "channel")
    if scope == "solo":
        game = bot.solo_games.get(uid)
        if not game:
            cached = _load_finished_state(payload)
            if cached:
                return {"ok": True, "state": cached["state"]}
            return {"ok": False, "error": "No active solo game found for this user."}
        return {"ok": True, "state": _snapshot_from_game(bot, game, "solo", uid, skip_profile_fetch=True)}

    cid = int(payload.get("cid", 0))
    game = bot.custom_games.get(cid) or bot.games.get(cid)
    if not game:
        cached = _load_finished_state(payload)
        if cached:
            return {"ok": True, "state": cached["state"]}
        return {"ok": False, "error": "No active channel game in this chat."}
    return {"ok": True, "state": _snapshot_from_game(bot, game, "channel", uid, skip_profile_fetch=True)}


class _WebGuessContext:
    def __init__(self, bot, channel, guild, author):
        self.bot = bot
        self.channel = channel
        self.guild = guild
        self.author = author
        self.ephemeral_messages = []

    async def defer(self):
        return None

    async def send(self, content=None, *, embed=None, view=None, ephemeral=False):
        if ephemeral:
            if content:
                self.ephemeral_messages.append(content)
            elif embed is not None:
                self.ephemeral_messages.append(embed.description or embed.title or "Request rejected.")
            return
        async def _deliver():
            await self.channel.send(content=content, embed=embed, view=view)

        task = asyncio.create_task(_deliver())
        if hasattr(self.bot, "_handle_task_exception"):
            task.add_done_callback(self.bot._handle_task_exception)


async def _submit_channel_guess(bot, payload: Dict[str, Any], word: str) -> Dict[str, Any]:
    uid = int(payload["uid"])
    cid = int(payload["cid"])

    game = bot.custom_games.get(cid) or bot.games.get(cid)
    if not game:
        return {"ok": False, "error": "No active channel game found."}

    channel = bot.get_channel(cid)
    if channel is None:
        return {"ok": False, "error": "Channel is not available to the bot."}

    author = bot.get_user(uid)
    if author is None:
        known_user = _get_known_integration_user(uid)
        if known_user:
            class _AvatarProxy:
                def __init__(self, url: str):
                    self.url = str(url or "")

            class _AuthorProxy:
                def __init__(self, user_id: int, name: str, avatar_url: str):
                    self.id = int(user_id)
                    self.display_name = str(name or user_id)
                    self.display_avatar = _AvatarProxy(avatar_url)

            author = _AuthorProxy(uid, str(known_user.get("name", "")), str(known_user.get("avatar_url", "")))
        else:
            try:
                author = await bot.fetch_user(uid)
            except Exception:
                return {"ok": False, "error": "Could not resolve your Discord user."}

    ctx = _WebGuessContext(bot=bot, channel=channel, guild=channel.guild, author=author)
    before_attempts = int(getattr(game, "attempts_used", 0))

    cog = bot.get_cog("GuessHandler")
    if cog is None:
        return {"ok": False, "error": "Guess handler is unavailable."}

    await cog._handle_guess_ctx(ctx, word.strip())

    after_attempts = int(getattr(game, "attempts_used", 0))
    if after_attempts == before_attempts and ctx.ephemeral_messages:
        return {"ok": False, "error": ctx.ephemeral_messages[-1]}

    state = _snapshot_from_game(bot, game, "channel", uid, skip_profile_fetch=True)
    guess_row_index = None
    for idx in range(len(state.get("rows", [])) - 1, -1, -1):
        row = state["rows"][idx]
        row_uid = int(((row.get("user") or {}).get("id", 0)) or 0)
        if row_uid == uid and str(row.get("word", "")).upper() == str(word or "").strip().upper():
            guess_row_index = idx
            break
    if state.get("game_over"):
        # Fetch full profile for end state (includes WR)
        full_state = _snapshot_from_game(bot, game, "channel", uid, skip_profile_fetch=False)
        retry_meta = {
            "scope": "channel",
            "is_classic": bool(getattr(game, "difficulty", 0) == 1),
            "hard_mode": bool(getattr(game, "hard_mode", False)),
            "is_custom": bool(getattr(game, "difficulty", 0) == 2),
            "cid": cid,
            "uid": uid,
        }
        _cache_finished_state(payload, full_state, retry_meta)
        state = full_state  # Use full state with WR for end state
    return {"ok": True, "state": state, "guess_row_index": guess_row_index}


async def _submit_solo_guess(bot, payload: Dict[str, Any], word: str) -> Dict[str, Any]:
    uid = int(payload["uid"])
    game = bot.solo_games.get(uid)
    if not game:
        return {"ok": False, "error": "No active solo game found."}

    guess = (word or "").strip().lower()
    if len(guess) != 5 or not guess.isalpha():
        return {"ok": False, "error": "5 letters only."}
    if guess not in bot.all_valid_5:
        return {"ok": False, "error": f"{guess.upper()} is not in dictionary."}
    if game.is_duplicate(guess):
        return {"ok": False, "error": f"{guess.upper()} already guessed."}

    user = bot.get_user(uid)
    if user is None:
        try:
            user = await bot.fetch_user(uid)
        except Exception:
            return {"ok": False, "error": "Could not resolve your Discord user."}

    _, _, game_over = game.process_turn(guess, user)
    state = _snapshot_from_game(bot, game, "solo", uid, skip_profile_fetch=True)
    if game_over:
        # Fetch full profile for end state (includes WR)
        state = _snapshot_from_game(bot, game, "solo", uid, skip_profile_fetch=False)
        bot.solo_games.pop(uid, None)
        retry_meta = {"scope": "solo", "uid": uid}
        _cache_finished_state(payload, state, retry_meta)
    return {"ok": True, "state": state, "guess_row_index": max(0, int(getattr(game, "attempts_used", 1)) - 1)}


class _RetryStartContext:
    def __init__(self, channel, guild, author):
        self.channel = channel
        self.guild = guild
        self.author = author

    async def defer(self):
        return None

    async def send(self, content=None, *, embed=None, view=None, ephemeral=False):
        await self.channel.send(content=content, embed=embed, view=view)


async def _retry_session(bot, payload: Dict[str, Any]) -> Dict[str, Any]:
    scope = payload.get("scope", "channel")
    uid = int(payload.get("uid", 0))
    cached = _load_finished_state(payload)
    if not cached:
        return {"ok": False, "error": "No finished session found for retry."}
    retry_meta = cached.get("retry_meta", {})

    if scope == "solo":
        from src.game import WordleGame

        author = bot.get_user(uid)
        if author is None:
            try:
                author = await bot.fetch_user(uid)
            except Exception:
                return {"ok": False, "error": "Could not resolve user for solo retry."}

        if uid in bot.solo_games:
            game = bot.solo_games[uid]
            return {"ok": True, "state": _snapshot_from_game(bot, game, "solo", uid, skip_profile_fetch=True)}

        secret_pool = bot.secrets or []
        if not secret_pool:
            return {"ok": False, "error": "No solo word list available."}

        import random

        game = WordleGame(random.choice(secret_pool), 0, author, 0)
        bot.solo_games[uid] = game
        _FINISHED_STATE_CACHE.pop(_session_cache_key(payload), None)
        return {"ok": True, "state": _snapshot_from_game(bot, game, "solo", uid, skip_profile_fetch=True)}

    if bool(retry_meta.get("is_custom", False)):
        return {"ok": False, "error": "Custom retry is not automated yet. Start custom game again in Discord."}

    cid = int(payload.get("cid", 0))
    if cid in bot.games or cid in bot.custom_games:
        game = bot.custom_games.get(cid) or bot.games.get(cid)
        current = _snapshot_from_game(bot, game, "channel", uid)
        # If an active game exists, reuse it; if it is finished, replace it with a new one.
        if not current.get("game_over"):
            return {"ok": True, "state": current}
        bot.custom_games.pop(cid, None)
        bot.games.pop(cid, None)

    channel = bot.get_channel(cid)
    if channel is None:
        return {"ok": False, "error": "Channel is unavailable for retry."}

    author = bot.get_user(uid)
    if author is None:
        try:
            author = await bot.fetch_user(uid)
        except Exception:
            return {"ok": False, "error": "Could not resolve user for retry."}

    from src.handlers.game_logic import start_multiplayer_game

    ctx = _RetryStartContext(channel=channel, guild=channel.guild, author=author)
    await start_multiplayer_game(
        bot,
        ctx,
        is_classic=bool(retry_meta.get("is_classic", True)),
        hard_mode=bool(retry_meta.get("hard_mode", False)),
    )

    game = bot.custom_games.get(cid) or bot.games.get(cid)
    if not game:
        # start_multiplayer_game posts first and registers immediately after; allow a brief settle window.
        for _ in range(20):
            await asyncio.sleep(0.1)
            game = bot.custom_games.get(cid) or bot.games.get(cid)
            if game:
                break
    if not game:
        return {"ok": False, "error": "Retry started but game state is unavailable."}

    _FINISHED_STATE_CACHE.pop(_session_cache_key(payload), None)
    return {"ok": True, "state": _snapshot_from_game(bot, game, "channel", uid, skip_profile_fetch=True)}

def _run_on_bot_loop(bot, coro):
    fut = asyncio.run_coroutine_threadsafe(coro, bot.loop)
    return fut.result(timeout=20)


def _create_app(bot):
    from flask import Flask, jsonify, redirect, render_template, request

    base_dir = Path(__file__).resolve().parent
    app = Flask(
        __name__,
        template_folder=str(base_dir / "templates"),
        static_folder=str(base_dir / "static"),
        static_url_path="/integration/static",
    )
    # Accept both `/path` and `/path/` so Discord path normalization doesn't 404.
    app.url_map.strict_slashes = False

    @app.before_request
    def _integration_debug_request_log():
        ua = (request.headers.get("user-agent") or "")[:120]
        print(f"[INT] {request.method} {request.full_path} ua={ua}")

    def _debug_echo_enabled() -> bool:
        return str(os.getenv("INTEGRATION_DEBUG_ECHO", "0")).strip().lower() in {"1", "true", "yes", "on"}

    def _render_debug_echo(note: str = "matched-route"):
        host = request.headers.get("host", "")
        ua = request.headers.get("user-agent", "")
        forwarded_host = request.headers.get("x-forwarded-host", "")
        forwarded_proto = request.headers.get("x-forwarded-proto", "")
        method = request.method
        full_url = request.url
        query = request.query_string.decode("utf-8", errors="ignore")
        path = request.path
        html = f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Integration Debug Echo</title>
<style>
body {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; background:#111; color:#eee; padding:18px; }}
.box {{ background:#1b1b1b; border:1px solid #333; border-radius:8px; padding:14px; max-width:1100px; }}
h1 {{ font-size:16px; margin:0 0 10px 0; }}
pre {{ white-space:pre-wrap; word-break:break-word; margin:0; line-height:1.45; }}
</style></head>
<body>
  <div class="box">
    <h1>Integration Debug Echo ({note})</h1>
    <pre>method: {method}
path: {path}
query: {query}
full_url: {full_url}
host: {host}
x-forwarded-host: {forwarded_host}
x-forwarded-proto: {forwarded_proto}
user-agent: {ua}</pre>
  </div>
</body></html>"""
        return html, 200

    @app.get("/")
    def root_redirect():
        return redirect("/integration/activity", code=302)

    @app.get("/integration")
    def integration_root_redirect():
        return redirect("/integration/activity", code=302)

    @app.get("/integration/wordle")
    @app.get("/wordle")
    def wordle_page():
        if _debug_echo_enabled():
            return _render_debug_echo("integration-wordle")
        token = request.args.get("token", "")
        if not _verify_token(token):
            return "Invalid or expired integration token.", 403
        return render_template(
            "wordle_integration.html",
            token=token,
            activity_mode=False,
            activity_client_id="",
        )

    @app.get("/integration/activity")
    @app.get("/activity")
    def activity_page():
        if _debug_echo_enabled():
            return _render_debug_echo("integration-activity")
        return render_template(
            "wordle_integration.html",
            token="",
            activity_mode=True,
            activity_client_id=_activity_client_id(),
        )

    @app.post("/integration/api/activity/oauth-token")
    @app.post("/integration/activity/api/activity/oauth-token")
    @app.post("/api/activity/oauth-token")
    @app.post("/activity/api/activity/oauth-token")
    def api_activity_oauth_token():
        data = request.get_json(silent=True) or {}
        code = str(data.get("code", "")).strip()
        redirect_uri = str(data.get("redirect_uri", "")).strip()
        if not code or not redirect_uri:
            return jsonify({"ok": False, "error": "Missing OAuth code or redirect URI."}), 400
        try:
            token_payload = _exchange_activity_oauth_code(code, redirect_uri)
            access_token = token_payload.get("access_token")
            if not access_token:
                return jsonify({"ok": False, "error": "No access token returned by Discord."}), 400
            return jsonify({"ok": True, "access_token": access_token})
        except Exception as exc:
            return jsonify({"ok": False, "error": f"OAuth exchange failed: {exc}"}), 400

    @app.post("/integration/api/activity/session-token")
    @app.post("/integration/activity/api/activity/session-token")
    @app.post("/api/activity/session-token")
    @app.post("/activity/api/activity/session-token")
    def api_activity_session_token():
        data = request.get_json(silent=True) or {}
        access_token = str(data.get("access_token", "")).strip()
        channel_id_raw = data.get("channel_id")
        location_id_raw = data.get("location_id")
        scope = str(data.get("scope", "channel")).strip().lower() or "channel"

        if not access_token:
            return jsonify({"ok": False, "error": "Missing access token."}), 400

        try:
            me = _fetch_discord_user(access_token)
            uid = int(me.get("id", 0))
            if uid <= 0:
                return jsonify({"ok": False, "error": "Invalid Discord user identity."}), 401
            _cache_known_integration_user(uid, me)
        except Exception as exc:
            return jsonify({"ok": False, "error": f"Token validation failed: {exc}"}), 401

        if scope == "solo" or (scope == "channel" and not channel_id_raw and uid in bot.solo_games):
            if uid not in bot.solo_games:
                return jsonify({"ok": False, "error": "No active solo game for this user."}), 404
            payload = {"uid": uid, "scope": "solo", "exp": int(time.time()) + 7200}
            return jsonify({"ok": True, "token": _sign_token(payload), "scope": "solo"})

        def _coerce_channel_id(value):
            if value is None:
                return None
            raw = str(value).strip()
            if raw.isdigit():
                return int(raw)
            import re
            matches = re.findall(r"\d{16,22}", raw)
            if matches:
                # Discord compound location identifiers may contain guild + channel ids;
                # the channel id is typically the final snowflake.
                return int(matches[-1])
            return None

        cid = _coerce_channel_id(channel_id_raw)
        if cid is None:
            cid = _coerce_channel_id(location_id_raw)

        try:
            if cid is None:
                raise ValueError("missing channel id")
        except Exception:
            return jsonify({"ok": False, "error": "Missing or invalid channel_id/location_id for channel mode."}), 400

        if cid not in bot.games and cid not in bot.custom_games:
            return jsonify({"ok": False, "error": "No active channel/custom game found."}), 404

        payload = {"uid": uid, "cid": cid, "scope": "channel", "exp": int(time.time()) + 7200}
        return jsonify({"ok": True, "token": _sign_token(payload), "scope": "channel"})

    @app.get("/integration/api/state")
    @app.get("/integration/activity/api/state")
    @app.get("/api/state")
    @app.get("/activity/api/state")
    def api_state():
        token = request.args.get("token", "")
        payload = _verify_token(token)
        if not payload:
            return jsonify({"ok": False, "error": "Invalid or expired token."}), 403
        try:
            result = _run_on_bot_loop(bot, _load_snapshot(bot, payload))
            
            # Spawn background profile fetch while user interacts
            if result.get("ok") and result.get("state"):
                try:
                    uid = int(payload.get("uid", 0))
                    if uid:
                        asyncio.run_coroutine_threadsafe(_background_cache_profile(bot, uid), bot.loop)
                except Exception:
                    pass
            
            return jsonify(result), (200 if result.get("ok") else 404)
        except Exception as exc:
            return jsonify({"ok": False, "error": f"State fetch failed: {exc}"}), 500

    @app.post("/integration/api/guess")
    @app.post("/integration/activity/api/guess")
    @app.post("/api/guess")
    @app.post("/activity/api/guess")
    def api_guess():
        data = request.get_json(silent=True) or {}
        token = data.get("token", "")
        word = data.get("word", "")
        payload = _verify_token(token)
        if not payload:
            return jsonify({"ok": False, "error": "Invalid or expired token."}), 403

        try:
            scope = payload.get("scope", "channel")
            if scope == "solo":
                result = _run_on_bot_loop(bot, _submit_solo_guess(bot, payload, word))
            else:
                result = _run_on_bot_loop(bot, _submit_channel_guess(bot, payload, word))
            
            # Spawn background profile fetch while animations play (~550ms)
            # This will populate cache by the time next request comes in
            if result.get("ok") and result.get("state"):
                try:
                    uid = int(payload.get("uid", 0))
                    if uid:
                        # Fire and forget: fetch profile in background on bot's event loop
                        asyncio.run_coroutine_threadsafe(_background_cache_profile(bot, uid), bot.loop)
                except Exception:
                    pass  # Don't let background task errors affect response
            
            return jsonify(result), (200 if result.get("ok") else 400)
        except Exception as exc:
            return jsonify({"ok": False, "error": f"Guess submit failed: {exc}"}), 500

    @app.post("/integration/api/retry")
    @app.post("/integration/activity/api/retry")
    @app.post("/api/retry")
    @app.post("/activity/api/retry")
    def api_retry():
        data = request.get_json(silent=True) or {}
        token = data.get("token", "")
        payload = _verify_token(token)
        if not payload:
            return jsonify({"ok": False, "error": "Invalid or expired token."}), 403
        try:
            result = _run_on_bot_loop(bot, _retry_session(bot, payload))
            
            # Spawn background profile fetch for cache warming
            if result.get("ok") and result.get("state"):
                try:
                    uid = int(payload.get("uid", 0))
                    if uid:
                        asyncio.run_coroutine_threadsafe(_background_cache_profile(bot, uid), bot.loop)
                except Exception:
                    pass
            
            return jsonify(result), (200 if result.get("ok") else 400)
        except Exception as exc:
            return jsonify({"ok": False, "error": f"Retry failed: {exc}"}), 500

    @app.route("/<path:any_path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
    def debug_echo_unmatched(any_path: str):
        """
        Temporary debugging helper: show exactly what URL/path Discord is hitting.
        Enable with INTEGRATION_DEBUG_ECHO=1.
        """
        if not _debug_echo_enabled():
            req_path = (request.path or "").lower()
            if req_path.startswith("/api/") or req_path.startswith("/integration/api/") or req_path.startswith("/integration/activity/api/") or req_path.startswith("/activity/api/"):
                return jsonify({"ok": False, "error": f"Endpoint not found: {request.path}"}), 404
            return "Not Found", 404
        return _render_debug_echo("unmatched-path")

    return app


def integration_base_url() -> str:
    return os.getenv("INTEGRATION_BASE_URL", "http://127.0.0.1:8787").rstrip("/")


def build_integration_link(bot, user_id: int, channel_id: int) -> Optional[str]:
    if channel_id in bot.custom_games or channel_id in bot.games:
        payload = {"uid": int(user_id), "cid": int(channel_id), "scope": "channel", "exp": int(time.time()) + 7200}
    elif user_id in bot.solo_games:
        payload = {"uid": int(user_id), "scope": "solo", "exp": int(time.time()) + 7200}
    else:
        return None

    token = _sign_token(payload)
    return f"{integration_base_url()}/integration/wordle?token={quote(token)}"


def start_integration_server(bot):
    global _SERVER_LOCK, _SERVER_THREAD, _SERVER_STARTED
    with _SERVER_LOCK:
        if _SERVER_STARTED:
            return

        host = os.getenv("INTEGRATION_HOST", "0.0.0.0")
        # For deployed environments (Render, Cloud Run): use PORT. For local dev: use INTEGRATION_PORT or default 8787
        port_raw = os.getenv("PORT") or os.getenv("INTEGRATION_PORT") or "8787"
        try:
            port = int(port_raw)
        except ValueError:
            raise RuntimeError(f"Invalid integration port value: {port_raw!r}")
        from waitress import serve
        app = _create_app(bot)

        def _run():
            print(f"🌐 Integration server running on http://{host}:{port}")
            serve(app, host=host, port=port, threads=4)

        _SERVER_THREAD = threading.Thread(target=_run, name="wordle-integration-server", daemon=True)
        _SERVER_THREAD.start()
        _SERVER_STARTED = True
