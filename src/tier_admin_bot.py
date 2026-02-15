import asyncio
import datetime
import hashlib
import inspect
import json
import os
import random
import re
import secrets
import signal
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Any

import discord
from discord.ext import commands
from flask import Flask, redirect, render_template_string, request, session, url_for
from supabase import Client, create_client
from werkzeug.middleware.proxy_fix import ProxyFix

from src.config import SUPABASE_KEY, SUPABASE_URL, TIERS


def _env_int(name: str, default: Optional[int] = None) -> Optional[int]:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class AdminStorage:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS templates (
                        name TEXT PRIMARY KEY,
                        content TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schedules (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        mode TEXT NOT NULL,
                        channel_id INTEGER NOT NULL,
                        sender_mode TEXT NOT NULL,
                        content TEXT NOT NULL,
                        custom_name TEXT,
                        custom_avatar_url TEXT,
                        run_at_utc TEXT,
                        daily_time_utc TEXT,
                        vars_json TEXT,
                        active INTEGER NOT NULL DEFAULT 1,
                        last_run_date_utc TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS scripts (
                        name TEXT PRIMARY KEY,
                        code TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                conn.commit()
            finally:
                conn.close()

    def save_template(self, name: str, content: str):
        now = datetime.datetime.utcnow().isoformat()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO templates(name, content, updated_at)
                    VALUES(?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        content=excluded.content,
                        updated_at=excluded.updated_at
                    """,
                    (name, content, now),
                )
                conn.commit()
            finally:
                conn.close()

    def list_templates(self) -> List[dict]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute("SELECT name, content, updated_at FROM templates ORDER BY name").fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def get_template(self, name: str) -> Optional[dict]:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT name, content, updated_at FROM templates WHERE name=?",
                    (name,),
                ).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()

    def delete_template(self, name: str):
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM templates WHERE name=?", (name,))
                conn.commit()
            finally:
                conn.close()

    def create_schedule(
        self,
        mode: str,
        channel_id: int,
        sender_mode: str,
        content: str,
        custom_name: str,
        custom_avatar_url: str,
        run_at_utc: Optional[str],
        daily_time_utc: Optional[str],
        vars_json: str,
    ) -> int:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    """
                    INSERT INTO schedules(
                        mode, channel_id, sender_mode, content, custom_name, custom_avatar_url,
                        run_at_utc, daily_time_utc, vars_json, active
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        mode,
                        channel_id,
                        sender_mode,
                        content,
                        custom_name,
                        custom_avatar_url,
                        run_at_utc,
                        daily_time_utc,
                        vars_json,
                    ),
                )
                conn.commit()
                return int(cur.lastrowid)
            finally:
                conn.close()

    def list_schedules(self) -> List[dict]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT id, mode, channel_id, sender_mode, content, run_at_utc, daily_time_utc, active, last_run_date_utc
                    FROM schedules
                    ORDER BY id DESC
                    """
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def due_schedules(self, now_utc: datetime.datetime) -> List[dict]:
        now_iso = now_utc.isoformat()
        today = now_utc.date().isoformat()
        hhmm = now_utc.strftime("%H:%M")
        with self._lock:
            conn = self._connect()
            try:
                rows_once = conn.execute(
                    """
                    SELECT * FROM schedules
                    WHERE active=1
                      AND mode IN ('once', 'fixed_date')
                      AND run_at_utc IS NOT NULL
                      AND run_at_utc <= ?
                    """,
                    (now_iso,),
                ).fetchall()
                rows_daily = conn.execute(
                    """
                    SELECT * FROM schedules
                    WHERE active=1
                      AND mode='daily'
                      AND daily_time_utc = ?
                      AND (last_run_date_utc IS NULL OR last_run_date_utc <> ?)
                    """,
                    (hhmm, today),
                ).fetchall()
                rows = [dict(r) for r in rows_once] + [dict(r) for r in rows_daily]
                return rows
            finally:
                conn.close()

    def mark_schedule_run(self, schedule_id: int, mode: str, run_date_utc: str):
        with self._lock:
            conn = self._connect()
            try:
                if mode in {"once", "fixed_date"}:
                    conn.execute("UPDATE schedules SET active=0 WHERE id=?", (schedule_id,))
                else:
                    conn.execute(
                        "UPDATE schedules SET last_run_date_utc=? WHERE id=?",
                        (run_date_utc, schedule_id),
                    )
                conn.commit()
            finally:
                conn.close()

    def delete_schedule(self, schedule_id: int):
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))
                conn.commit()
            finally:
                conn.close()

    def save_script(self, name: str, code: str):
        now = datetime.datetime.utcnow().isoformat()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO scripts(name, code, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        code=excluded.code,
                        updated_at=excluded.updated_at
                    """,
                    (name, code, now),
                )
                conn.commit()
            finally:
                conn.close()

    def list_scripts(self) -> List[dict]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute("SELECT name, updated_at FROM scripts ORDER BY name").fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def get_script(self, name: str) -> Optional[dict]:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT name, code, updated_at FROM scripts WHERE name=?", (name,)).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()

    def delete_script(self, name: str):
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM scripts WHERE name=?", (name,))
                conn.commit()
            finally:
                conn.close()


@dataclass(frozen=True)
class TierRole:
    name: str
    min_wr: int
    color: discord.Color


TIER_ROLE_ORDER: List[TierRole] = [
    TierRole(name="Legend", min_wr=3900, color=discord.Color.from_rgb(255, 215, 0)),
    TierRole(name="Grandmaster", min_wr=2800, color=discord.Color.from_rgb(0, 191, 255)),
    TierRole(name="Master", min_wr=1600, color=discord.Color.from_rgb(156, 39, 176)),
    TierRole(name="Elite", min_wr=900, color=discord.Color.from_rgb(244, 67, 54)),
    TierRole(name="Challenger", min_wr=0, color=discord.Color.from_rgb(96, 125, 139)),
]

LEVEL_ROLE_PREFIX = "wordle level "
LEVEL_BUCKET_STEP = 5
LEVEL_MIN_BUCKET = 10


def tier_from_wr(wr: int) -> Optional[str]:
    for t in TIERS:
        if wr >= int(t["min_wr"]):
            return t["name"]
    return None


def parse_hex_color(hex_color: str) -> discord.Color:
    cleaned = hex_color.strip().lstrip("#")
    if len(cleaned) != 6:
        raise ValueError("Color must be a 6-digit hex value like #22aa88")
    return discord.Color(int(cleaned, 16))


def level_color_for_bucket(bucket: int) -> discord.Color:
    # Deterministic "random fixed" color per bucket: same bucket => same color everywhere.
    digest = hashlib.sha256(f"wordle-level-{bucket}".encode("utf-8")).hexdigest()
    hue = int(digest[:8], 16) % 360
    sat = 72
    val = 88

    h = hue / 60.0
    c = (val / 100.0) * (sat / 100.0)
    x = c * (1 - abs((h % 2) - 1))
    m = (val / 100.0) - c

    if 0 <= h < 1:
        rp, gp, bp = c, x, 0
    elif 1 <= h < 2:
        rp, gp, bp = x, c, 0
    elif 2 <= h < 3:
        rp, gp, bp = 0, c, x
    elif 3 <= h < 4:
        rp, gp, bp = 0, x, c
    elif 4 <= h < 5:
        rp, gp, bp = x, 0, c
    else:
        rp, gp, bp = c, 0, x

    r = int((rp + m) * 255)
    g = int((gp + m) * 255)
    b = int((bp + m) * 255)
    return discord.Color.from_rgb(r, g, b)


class TierAdminBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True

        super().__init__(command_prefix=[], intents=intents, max_messages=25)

        self.guild_id = _env_int("TIER_BOT_GUILD_ID")
        self.role_prefix = os.getenv("TIER_ROLE_PREFIX", "Tier")
        self.sync_interval_seconds = _env_int("TIER_BOT_SYNC_INTERVAL_SECONDS", 3600) or 3600
        self.member_update_delay = _env_float("TIER_BOT_MEMBER_UPDATE_DELAY_SECONDS", 1.0)
        # Full-format mentions are enabled by default to match admin panel expectations.
        # Set TIER_BOT_ALLOW_ALL_MENTIONS=0 to restrict @everyone/@here/reply mentions.
        allow_all_mentions = _env_bool("TIER_BOT_ALLOW_ALL_MENTIONS", True)
        self.allowed_mentions = (
            discord.AllowedMentions(everyone=True, roles=True, users=True, replied_user=True)
            if allow_all_mentions
            else discord.AllowedMentions(everyone=False, roles=True, users=True, replied_user=False)
        )

        self.supabase_client: Optional[Client] = None
        self.pending_login_codes: Dict[int, Tuple[str, float]] = {}
        self._background_tasks: Dict[str, asyncio.Task] = {}
        self._channel_cache: Tuple[float, List[discord.TextChannel]] = (0.0, [])
        self._webhook_cache: Dict[int, int] = {}
        self.storage = AdminStorage(os.getenv("TIER_BOT_STORAGE_PATH", "admin_panel.db"))
        self.last_schedule_run_info = "No schedule runs yet."

    async def setup_hook(self):
        if not self.guild_id:
            raise RuntimeError("TIER_BOT_GUILD_ID is required.")
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise RuntimeError("SUPABASE_URL and SUPABASE_KEY are required.")

        self.supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
        self._background_tasks["hourly_sync"] = asyncio.create_task(self.hourly_tier_sync_loop())
        self._background_tasks["schedule_runner"] = asyncio.create_task(self.schedule_runner_loop())
        print("✅ TierAdminBot setup complete.")

    async def close(self):
        for task in self._background_tasks.values():
            task.cancel()
        await super().close()

    def get_latency_ms(self) -> float:
        return round(self.latency * 1000, 2) if self.latency is not None else -1.0

    def _render_template_text(self, text: str, variables: Dict[str, Any]) -> str:
        defaults = {
            "now_utc": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
            "guild_id": str(self.guild_id or ""),
            "bot_name": str(self.user.name if self.user else "TierAdminBot"),
        }
        merged = {**defaults, **{k: str(v) for k, v in (variables or {}).items()}}

        def repl(match):
            key = match.group(1)
            return merged.get(key, match.group(0))

        return re.sub(r"\{([a-zA-Z0-9_]+)\}", repl, text)

    async def get_target_guild(self) -> discord.Guild:
        guild = self.get_guild(self.guild_id)
        if not guild:
            guild = await self.fetch_guild(self.guild_id)
        if guild is None:
            raise RuntimeError("Unable to fetch target guild.")
        return guild

    async def _ensure_tier_roles(self, guild: discord.Guild) -> Dict[str, discord.Role]:
        wanted = {f"{self.role_prefix} {t.name}": t for t in TIER_ROLE_ORDER}
        by_name = {r.name: r for r in guild.roles}
        output: Dict[str, discord.Role] = {}

        for role_name, tier in wanted.items():
            role = by_name.get(role_name)
            if role is None:
                role = await guild.create_role(
                    name=role_name,
                    color=tier.color,
                    reason="Create tier role for automated role sync",
                )
            elif role.color.value != tier.color.value:
                await role.edit(color=tier.color, reason="Normalize tier role color")
            output[tier.name] = role
        return output

    async def _fetch_member_wr_map(self, member_ids: List[int]) -> Dict[int, int]:
        if not member_ids:
            return {}
        assert self.supabase_client is not None

        response = (
            self.supabase_client.table("user_stats_v2")
            .select("user_id,multi_wr")
            .in_("user_id", member_ids)
            .execute()
        )

        wr_map: Dict[int, int] = {}
        for row in response.data or []:
            uid = int(row["user_id"])
            wr_map[uid] = int(row.get("multi_wr", 0))
        return wr_map

    async def _fetch_member_stats_map(self, member_ids: List[int]) -> Dict[int, dict]:
        if not member_ids:
            return {}
        assert self.supabase_client is not None

        response = (
            self.supabase_client.table("user_stats_v2")
            .select("user_id,multi_wr,xp")
            .in_("user_id", member_ids)
            .execute()
        )

        stats: Dict[int, dict] = {}
        for row in response.data or []:
            uid = int(row["user_id"])
            stats[uid] = {
                "wr": int(row.get("multi_wr", 0) or 0),
                "xp": int(row.get("xp", 0) or 0),
            }
        return stats

    def _get_level_from_xp(self, total_xp: int) -> int:
        lvl = 1
        curr = int(total_xp or 0)
        if curr < 0:
            curr = 0

        if curr >= 1000:
            lvl += 10
            curr -= 1000
        else:
            return lvl + (curr // 100)

        if curr >= 4000:
            lvl += 20
            curr -= 4000
        else:
            return lvl + (curr // 200)

        if curr >= 10500:
            lvl += 30
            curr -= 10500
        else:
            return lvl + (curr // 350)

        return lvl + (curr // 500)

    def _level_bucket_for_level(self, level: int) -> Optional[int]:
        if level < LEVEL_MIN_BUCKET:
            return None
        return (level // LEVEL_BUCKET_STEP) * LEVEL_BUCKET_STEP

    def _parse_level_bucket_from_role_name(self, role_name: str) -> Optional[int]:
        name = (role_name or "").strip().lower()
        if not name.startswith(LEVEL_ROLE_PREFIX):
            return None
        tail = role_name[len(LEVEL_ROLE_PREFIX):].strip()
        if tail.isdigit():
            return int(tail)
        return None

    async def run_tier_sync_once(self) -> dict:
        guild = await self.get_target_guild()
        if not guild.chunked:
            await guild.chunk(cache=True)
        tier_roles = await self._ensure_tier_roles(guild)
        tier_role_ids = {r.id for r in tier_roles.values()}

        members = [m for m in guild.members if not m.bot]
        member_ids = [m.id for m in members]
        stats_map = await self._fetch_member_stats_map(member_ids)

        # Ensure level roles for all required cumulative buckets exist.
        level_buckets = set()
        for member in members:
            stat = stats_map.get(member.id)
            if not stat:
                continue
            level = self._get_level_from_xp(stat.get("xp", 0))
            bucket = self._level_bucket_for_level(level)
            if bucket is not None:
                for b in range(LEVEL_MIN_BUCKET, bucket + 1, LEVEL_BUCKET_STEP):
                    level_buckets.add(b)

        by_name = {r.name.lower(): r for r in guild.roles}
        level_role_by_bucket: Dict[int, discord.Role] = {}
        for bucket in sorted(level_buckets):
            role_name = f"{LEVEL_ROLE_PREFIX}{bucket}"
            role = by_name.get(role_name)
            if role is None:
                role = await guild.create_role(
                    name=role_name,
                    color=level_color_for_bucket(bucket),
                    reason="Create level role for automated level sync",
                )
            elif role.color.value != level_color_for_bucket(bucket).value:
                await role.edit(
                    color=level_color_for_bucket(bucket),
                    reason="Normalize deterministic level role color",
                )
            level_role_by_bucket[bucket] = role

        existing_level_role_ids = {
            r.id for r in guild.roles if self._parse_level_bucket_from_role_name(r.name) is not None
        }

        changes = 0
        skipped = 0
        level_changes = 0
        level_skipped = 0
        for member in members:
            stat = stats_map.get(member.id, {"wr": 0, "xp": 0})
            wr = int(stat.get("wr", 0))
            tier_name = tier_from_wr(wr)
            target_role = tier_roles.get(tier_name) if tier_name else None
            current_tier_roles = [r for r in member.roles if r.id in tier_role_ids]

            has_target = target_role is not None and target_role in current_tier_roles
            only_target = has_target and len(current_tier_roles) == 1
            if only_target:
                skipped += 1
            else:
                try:
                    remove_list = [r for r in current_tier_roles if target_role is None or r.id != target_role.id]
                    if remove_list:
                        await member.remove_roles(*remove_list, reason="Tier sync update")
                    if target_role and target_role not in member.roles:
                        await member.add_roles(target_role, reason="Tier sync update")
                    changes += 1
                except discord.Forbidden:
                    pass
                except discord.HTTPException:
                    pass

            level = self._get_level_from_xp(int(stat.get("xp", 0)))
            target_bucket = self._level_bucket_for_level(level)
            current_level_buckets = set()
            for role in member.roles:
                b = self._parse_level_bucket_from_role_name(role.name)
                if b is not None:
                    current_level_buckets.add(b)

            desired_buckets = set()
            if target_bucket is not None:
                for b in range(LEVEL_MIN_BUCKET, target_bucket + 1, LEVEL_BUCKET_STEP):
                    desired_buckets.add(b)

            missing_buckets = sorted(desired_buckets - current_level_buckets)
            if not missing_buckets:
                level_skipped += 1
            else:
                try:
                    add_roles = [level_role_by_bucket[b] for b in missing_buckets if b in level_role_by_bucket]
                    if add_roles:
                        await member.add_roles(*add_roles, reason="Cumulative level sync update")
                        level_changes += 1
                except discord.Forbidden:
                    pass
                except discord.HTTPException:
                    pass

            await asyncio.sleep(max(0.2, self.member_update_delay))

        return {
            "members_scanned": len(members),
            "db_rows": len(stats_map),
            "changes": changes,
            "unchanged": skipped,
            "level_changes": level_changes,
            "level_unchanged": level_skipped,
        }

    async def hourly_tier_sync_loop(self):
        await self.wait_until_ready()
        while not self.is_closed():
            started = datetime.datetime.utcnow()
            try:
                result = await self.run_tier_sync_once()
                print(
                    "✅ Tier + level sync done",
                    f"at={started.isoformat()}",
                    f"scanned={result['members_scanned']}",
                    f"tier_changes={result['changes']}",
                    f"tier_unchanged={result['unchanged']}",
                    f"level_changes={result['level_changes']}",
                    f"level_unchanged={result['level_unchanged']}",
                )
            except Exception as exc:
                print(f"❌ Tier/level sync error: {exc}")

            await asyncio.sleep(self.sync_interval_seconds)

    async def is_guild_admin(self, user_id: int) -> bool:
        guild = await self.get_target_guild()
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.HTTPException:
                return False

        perms = member.guild_permissions
        return perms.administrator or perms.manage_guild

    async def issue_login_code(self, user_id: int) -> Tuple[bool, str]:
        self._cleanup_expired_codes()
        user = self.get_user(user_id)
        if user is None:
            try:
                user = await self.fetch_user(user_id)
            except discord.HTTPException:
                return False, "Unable to fetch that Discord user."

        if user is None:
            return False, "Unknown Discord user."

        code = f"{random.randint(0, 999999):06d}"
        expiry_ts = time.time() + 300
        self.pending_login_codes[user_id] = (code, expiry_ts)

        try:
            await user.send(f"Your Tier Admin login code is: `{code}` (valid for 5 minutes)")
            return True, "Code sent in DM."
        except discord.HTTPException:
            self.pending_login_codes.pop(user_id, None)
            return False, "Unable to send DM. Enable DMs from server members and retry."

    def verify_login_code(self, user_id: int, code: str) -> bool:
        self._cleanup_expired_codes()
        payload = self.pending_login_codes.get(user_id)
        if not payload:
            return False
        saved_code, expires = payload
        if time.time() > expires:
            self.pending_login_codes.pop(user_id, None)
            return False
        if secrets.compare_digest(saved_code, code.strip()):
            self.pending_login_codes.pop(user_id, None)
            return True
        return False

    def _cleanup_expired_codes(self):
        now = time.time()
        expired = [uid for uid, (_, expiry) in self.pending_login_codes.items() if now > expiry]
        for uid in expired:
            self.pending_login_codes.pop(uid, None)

    async def list_text_channels(self) -> List[discord.TextChannel]:
        now = time.time()
        ts, cached = self._channel_cache
        if now - ts < 60 and cached:
            return cached

        guild = await self.get_target_guild()
        me = guild.me or guild.get_member(self.user.id if self.user else 0)
        channels = [c for c in guild.text_channels if me and c.permissions_for(me).send_messages]
        channels.sort(key=lambda c: c.position)
        self._channel_cache = (now, channels)
        return channels

    async def _get_or_create_webhook(self, channel: discord.TextChannel) -> discord.Webhook:
        webhook: Optional[discord.Webhook] = None
        cached_id = self._webhook_cache.get(channel.id)
        if cached_id:
            hooks = await channel.webhooks()
            webhook = next((h for h in hooks if h.id == cached_id), None)

        if webhook is None:
            hooks = await channel.webhooks()
            webhook = next((h for h in hooks if h.user and h.user.id == self.user.id), None)
            if webhook is None:
                webhook = await channel.create_webhook(name="TierRelay")
            self._webhook_cache[channel.id] = webhook.id

        return webhook

    async def relay_message(
        self,
        channel_id: int,
        content: str,
        sender_mode: str,
        sender_user_id: int,
        custom_name: str = "",
        custom_avatar_url: str = "",
        template_vars: Optional[Dict[str, Any]] = None,
    ) -> str:
        guild = await self.get_target_guild()
        channel = guild.get_channel(channel_id)
        if channel is None or not isinstance(channel, discord.TextChannel):
            raise ValueError("Invalid text channel.")

        mode = (sender_mode or "").strip().lower()
        if mode not in {"me", "bot", "custom"}:
            raise ValueError("Invalid sender mode.")

        rendered_content = self._render_template_text(content, template_vars or {})

        if mode == "bot":
            await channel.send(content=rendered_content, allowed_mentions=self.allowed_mentions)
            return f"Message sent to #{channel.name} as bot."

        member = guild.get_member(sender_user_id)
        if member is None:
            try:
                member = await guild.fetch_member(sender_user_id)
            except discord.HTTPException:
                member = None

        if mode == "me":
            sender_name = member.display_name if member else "Relay"
            sender_avatar = member.display_avatar.url if member else None
        else:
            sender_name = (custom_name or "").strip()
            if not sender_name:
                raise ValueError("Custom profile name is required for custom mode.")
            sender_avatar = (custom_avatar_url or "").strip() or None

        webhook = await self._get_or_create_webhook(channel)

        await webhook.send(
            content=rendered_content,
            username=sender_name,
            avatar_url=sender_avatar,
            allowed_mentions=self.allowed_mentions,
            wait=True,
        )
        return f"Message relayed to #{channel.name} as {sender_name}."

    async def bulk_equip_badge(self, user_ids: List[int], badge_name: str, delay_seconds: float = 0.35) -> dict:
        assert self.supabase_client is not None
        badge = badge_name.strip()
        if not badge:
            raise ValueError("Badge name cannot be empty.")

        ok_count = 0
        fail_count = 0
        failed: List[int] = []
        for uid in user_ids:
            try:
                self.supabase_client.table("user_stats_v2").update({"active_badge": badge}).eq("user_id", uid).execute()
                ok_count += 1
            except Exception:
                fail_count += 1
                failed.append(uid)
            await asyncio.sleep(max(0.1, delay_seconds))

        return {"success": ok_count, "failed": fail_count, "failed_user_ids": failed}

    async def schedule_runner_loop(self):
        await self.wait_until_ready()
        while not self.is_closed():
            now = datetime.datetime.utcnow()
            try:
                due = self.storage.due_schedules(now)
                for row in due:
                    vars_payload = {}
                    if row.get("vars_json"):
                        try:
                            vars_payload = json.loads(row["vars_json"])
                        except Exception:
                            vars_payload = {}

                    try:
                        await self.relay_message(
                            channel_id=int(row["channel_id"]),
                            content=str(row["content"]),
                            sender_mode=str(row["sender_mode"]),
                            sender_user_id=0,
                            custom_name=str(row.get("custom_name") or ""),
                            custom_avatar_url=str(row.get("custom_avatar_url") or ""),
                            template_vars=vars_payload,
                        )
                        self.storage.mark_schedule_run(
                            schedule_id=int(row["id"]),
                            mode=str(row["mode"]),
                            run_date_utc=now.date().isoformat(),
                        )
                        self.last_schedule_run_info = f"{now.isoformat()} ok schedule_id={row['id']}"
                    except Exception as exc:
                        self.last_schedule_run_info = f"{now.isoformat()} failed schedule_id={row['id']} err={exc}"
            except Exception as exc:
                self.last_schedule_run_info = f"{now.isoformat()} scheduler_error={exc}"

            await asyncio.sleep(30)

    async def run_admin_script(self, script_name: str, args: Dict[str, Any]) -> str:
        payload = self.storage.get_script(script_name)
        if not payload:
            raise ValueError("Script not found.")

        code = payload["code"]
        safe_builtins = {
            "len": len,
            "range": range,
            "min": min,
            "max": max,
            "sum": sum,
            "str": str,
            "int": int,
            "float": float,
            "bool": bool,
            "list": list,
            "dict": dict,
            "set": set,
            "tuple": tuple,
            "enumerate": enumerate,
            "print": print,
        }

        class ScriptAPI:
            def __init__(self, bot: "TierAdminBot"):
                self.bot = bot
                self.logs: List[str] = []

            def log(self, text: str):
                self.logs.append(str(text))

            async def send_message(self, **kwargs):
                return await self.bot.relay_message(**kwargs)

            async def assign_role(self, user_id: int, role_name: str, color_hex: str):
                return await self.bot.assign_custom_role(user_id, role_name, color_hex)

            async def remove_roles(self, user_id: int, role_ids: List[int]):
                return await self.bot.remove_member_roles(user_id, role_ids)

            async def equip_badge(self, user_id: int, badge_name: str):
                return await self.bot.equip_badge(user_id, badge_name)

            async def tier_sync(self):
                return await self.bot.run_tier_sync_once()

        env_globals = {"__builtins__": safe_builtins}
        env_locals: Dict[str, Any] = {}
        exec(code, env_globals, env_locals)
        fn = env_locals.get("run") or env_globals.get("run")
        if not callable(fn):
            raise ValueError("Script must define callable run(api, args).")

        api = ScriptAPI(self)
        result = fn(api, args)
        if inspect.isawaitable(result):
            result = await result

        return f"result={result} logs={api.logs}"

    async def assign_custom_role(self, user_id: int, role_name: str, color_hex: str) -> str:
        guild = await self.get_target_guild()
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.HTTPException:
                raise ValueError("Member not found in target guild.")

        role = discord.utils.get(guild.roles, name=role_name.strip())
        color = parse_hex_color(color_hex)
        if role is None:
            role = await guild.create_role(
                name=role_name.strip(),
                color=color,
                mentionable=False,
                reason="Custom role assignment from admin panel",
            )
        elif role.color.value != color.value:
            await role.edit(color=color, reason="Update custom role color")

        await member.add_roles(role, reason="Custom role assignment from admin panel")
        return f"Assigned role `{role.name}` to `{member.display_name}`."

    async def list_member_roles(self, user_id: int) -> List[discord.Role]:
        guild = await self.get_target_guild()
        member = guild.get_member(user_id)
        if member is None:
            member = await guild.fetch_member(user_id)
        return [r for r in member.roles if r != guild.default_role]

    async def remove_member_roles(self, user_id: int, role_ids: List[int]) -> str:
        guild = await self.get_target_guild()
        member = guild.get_member(user_id)
        if member is None:
            member = await guild.fetch_member(user_id)

        roles = [guild.get_role(rid) for rid in role_ids]
        roles = [r for r in roles if r is not None]
        if not roles:
            return "No valid roles selected."

        await member.remove_roles(*roles, reason="Custom role removal from admin panel")
        return f"Removed {len(roles)} role(s) from `{member.display_name}`."

    async def equip_badge(self, user_id: int, badge_name: str) -> str:
        assert self.supabase_client is not None
        badge = badge_name.strip()
        if not badge:
            raise ValueError("Badge name cannot be empty.")

        self.supabase_client.table("user_stats_v2").update({"active_badge": badge}).eq("user_id", user_id).execute()
        return f"Badge `{badge}` equipped for user `{user_id}`."


ADMIN_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Tier Admin Panel</title>
  <style>
    body { font-family: ui-sans-serif, -apple-system, Segoe UI, sans-serif; margin: 20px; background: #f5f6f8; color: #111; }
    .box { background: #fff; border: 1px solid #ddd; border-radius: 10px; padding: 14px; margin-bottom: 14px; }
    label { display: block; margin: 6px 0; font-size: 14px; }
    input, select, textarea, button { width: 100%; padding: 8px; margin-top: 4px; box-sizing: border-box; }
    textarea.code { font-family: Consolas, monospace; min-height: 150px; }
    button { cursor: pointer; }
    .ok { color: #0a7a28; }
    .err { color: #b3261e; }
    .grid { display: grid; grid-template-columns: 1fr; gap: 14px; }
    .muted { color: #444; font-size: 13px; }
    .list { max-height: 180px; overflow-y: auto; border: 1px solid #eee; padding: 8px; border-radius: 8px; background: #fafafa; }
    @media (min-width: 900px) { .grid { grid-template-columns: 1fr 1fr; } }
  </style>
</head>
<body>
  <h2>Tier Admin Panel</h2>
  <p class="muted">Latency: <b>{{ latency_ms }} ms</b> | Last scheduler run: <b>{{ last_schedule_run_info }}</b></p>
  {% if message %}<p class="ok">{{ message }}</p>{% endif %}
  {% if error %}<p class="err">{{ error }}</p>{% endif %}
  <p><a href="{{ url_for('logout') }}">Logout</a></p>
  <div class="grid">
    <div class="box">
      <h3>Send Message</h3>
      <form method="post" action="{{ url_for('send_message') }}">
        <label>Channel
          <select name="channel_id" required>
            {% for ch in channels %}
              <option value="{{ ch.id }}">#{{ ch.name }}</option>
            {% endfor %}
          </select>
        </label>
        <label>Sender Mode
          <select name="sender_mode" required>
            <option value="me">As Me</option>
            <option value="bot">As Bot</option>
            <option value="custom">Custom Profile</option>
          </select>
        </label>
        <label>Template (optional)
          <select name="template_name">
            <option value="">(none)</option>
            {% for t in templates %}
              <option value="{{ t.name }}">{{ t.name }}</option>
            {% endfor %}
          </select>
        </label>
        <label>Template Variables JSON (optional)
          <textarea name="vars_json" rows="3" placeholder='{"user":"<@123>","event":"Tier reset"}'></textarea>
        </label>
        <label>Custom Profile Name (only for Custom Profile)
          <input type="text" name="custom_name" placeholder="Optional unless Custom Profile mode">
        </label>
        <label>Custom Avatar URL (optional)
          <input type="text" name="custom_avatar_url" placeholder="https://...">
        </label>
        <label>Content
          <textarea name="content" rows="5" required></textarea>
        </label>
        <button type="submit">Send</button>
      </form>
    </div>
    <div class="box">
      <h3>Templates</h3>
      <form method="post" action="{{ url_for('save_template') }}">
        <label>Template Name <input type="text" name="name" required></label>
        <label>Template Content
          <textarea name="content" rows="6" required></textarea>
        </label>
        <button type="submit">Save Template</button>
      </form>
      <p class="muted">Variables: <code>{now_utc}</code>, <code>{guild_id}</code>, <code>{bot_name}</code> and your JSON keys.</p>
      <div class="list">
        {% for t in templates %}
          <div>
            <b>{{ t.name }}</b>
            <form method="post" action="{{ url_for('delete_template') }}">
              <input type="hidden" name="name" value="{{ t.name }}">
              <button type="submit">Delete</button>
            </form>
          </div>
        {% endfor %}
      </div>
    </div>
    <div class="box">
      <h3>Schedules (UTC)</h3>
      <form method="post" action="{{ url_for('create_schedule') }}">
        <label>Mode
          <select name="mode" required>
            <option value="once">Once (after delay)</option>
            <option value="daily">Daily (UTC HH:MM)</option>
            <option value="fixed_date">Fixed Date/Time (UTC)</option>
          </select>
        </label>
        <label>Channel
          <select name="channel_id" required>
            {% for ch in channels %}
              <option value="{{ ch.id }}">#{{ ch.name }}</option>
            {% endfor %}
          </select>
        </label>
        <label>Sender Mode
          <select name="sender_mode" required>
            <option value="bot">As Bot</option>
            <option value="me">As Me</option>
            <option value="custom">Custom Profile</option>
          </select>
        </label>
        <label>Delay Minutes (for Once)
          <input type="number" name="delay_minutes" value="5" min="1">
        </label>
        <label>Daily Time UTC (HH:MM)
          <input type="text" name="daily_time_utc" placeholder="09:30">
        </label>
        <label>Fixed Date/Time UTC (YYYY-MM-DD HH:MM)
          <input type="text" name="fixed_datetime_utc" placeholder="2026-02-16 18:30">
        </label>
        <label>Template (optional)
          <select name="template_name">
            <option value="">(none)</option>
            {% for t in templates %}
              <option value="{{ t.name }}">{{ t.name }}</option>
            {% endfor %}
          </select>
        </label>
        <label>Template Variables JSON (optional)
          <textarea name="vars_json" rows="3" placeholder='{"event":"Daily update"}'></textarea>
        </label>
        <label>Custom Profile Name
          <input type="text" name="custom_name">
        </label>
        <label>Custom Avatar URL
          <input type="text" name="custom_avatar_url">
        </label>
        <label>Content
          <textarea name="content" rows="4" required></textarea>
        </label>
        <button type="submit">Create Schedule</button>
      </form>
      <div class="list">
        {% for s in schedules %}
          <div>
            <b>#{{ s.id }}</b> {{ s.mode }} | ch={{ s.channel_id }} | {{ "active" if s.active else "inactive" }}
            <form method="post" action="{{ url_for('delete_schedule') }}">
              <input type="hidden" name="schedule_id" value="{{ s.id }}">
              <button type="submit">Delete</button>
            </form>
          </div>
        {% endfor %}
      </div>
    </div>
    <div class="box">
      <h3>Assign Role</h3>
      <form method="post" action="{{ url_for('assign_role') }}">
        <label>User ID <input type="text" name="user_id" required></label>
        <label>Role Name <input type="text" name="role_name" required></label>
        <label>Role Color Hex <input type="text" name="color_hex" value="#22aa88" required></label>
        <button type="submit">Assign</button>
      </form>
    </div>
    <div class="box">
      <h3>Lookup Roles</h3>
      <form method="get" action="{{ url_for('index') }}">
        <label>User ID <input type="text" name="lookup_user_id" value="{{ lookup_user_id or '' }}" required></label>
        <button type="submit">Load Roles</button>
      </form>
      {% if lookup_roles %}
        <form method="post" action="{{ url_for('remove_roles') }}">
          <input type="hidden" name="user_id" value="{{ lookup_user_id }}">
          {% for role in lookup_roles %}
            <label><input type="checkbox" name="role_ids" value="{{ role.id }}"> {{ role.name }}</label>
          {% endfor %}
          <button type="submit">Remove Selected Roles</button>
        </form>
      {% endif %}
    </div>
    <div class="box">
      <h3>Equip Badge</h3>
      <form method="post" action="{{ url_for('equip_badge') }}">
        <label>User ID <input type="text" name="user_id" required></label>
        <label>Badge Name <input type="text" name="badge_name" required></label>
        <button type="submit">Equip</button>
      </form>
    </div>
    <div class="box">
      <h3>Bulk Badge</h3>
      <form method="post" action="{{ url_for('bulk_badge') }}">
        <label>Badge Name <input type="text" name="badge_name" required></label>
        <label>User IDs (one per line)
          <textarea name="user_ids_text" rows="6" required></textarea>
        </label>
        <button type="submit">Run Bulk Badge</button>
      </form>
    </div>
    <div class="box">
      <h3>Task Scripts</h3>
      <form method="post" action="{{ url_for('save_script') }}">
        <label>Script Name <input type="text" name="name" required></label>
        <label>Python Code (must define <code>run(api, args)</code>)
          <textarea class="code" name="code" required>async def run(api, args):
    # args is a dict from JSON
    result = await api.tier_sync()
    api.log(f"sync={result}")
    return "ok"</textarea>
        </label>
        <button type="submit">Save Script</button>
      </form>
      <form method="post" action="{{ url_for('run_script') }}">
        <label>Script Name
          <select name="script_name" required>
            {% for sc in scripts %}
              <option value="{{ sc.name }}">{{ sc.name }}</option>
            {% endfor %}
          </select>
        </label>
        <label>Args JSON
          <textarea name="args_json" rows="3" placeholder='{"channel_id":123}'></textarea>
        </label>
        <button type="submit">Run Script</button>
      </form>
      <div class="list">
        {% for sc in scripts %}
          <div>
            <b>{{ sc.name }}</b> ({{ sc.updated_at }})
            <form method="post" action="{{ url_for('delete_script') }}">
              <input type="hidden" name="name" value="{{ sc.name }}">
              <button type="submit">Delete</button>
            </form>
          </div>
        {% endfor %}
      </div>
    </div>
    <div class="box">
      <h3>Manual Tier Sync</h3>
      <form method="post" action="{{ url_for('run_sync') }}">
        <button type="submit">Run Now</button>
      </form>
    </div>
  </div>
</body>
</html>
"""


LOGIN_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Tier Admin Login</title>
  <style>
    body { font-family: ui-sans-serif, -apple-system, Segoe UI, sans-serif; margin: 20px; background: #f5f6f8; color: #111; }
    .box { max-width: 460px; background: #fff; border: 1px solid #ddd; border-radius: 10px; padding: 16px; }
    label { display: block; margin: 8px 0; }
    input, button { width: 100%; padding: 8px; margin-top: 4px; box-sizing: border-box; }
    .ok { color: #0a7a28; }
    .err { color: #b3261e; }
  </style>
</head>
<body>
  <div class="box">
    <h2>Tier Admin Login</h2>
    {% if message %}<p class="ok">{{ message }}</p>{% endif %}
    {% if error %}<p class="err">{{ error }}</p>{% endif %}
    <form method="post" action="{{ url_for('start_login') }}">
      <label>Discord User ID
        <input type="text" name="user_id" required>
      </label>
      <button type="submit">Send DM Code</button>
    </form>
    <hr>
    <form method="post" action="{{ url_for('verify_login') }}">
      <label>Discord User ID
        <input type="text" name="user_id" required>
      </label>
      <label>6-digit Code
        <input type="text" name="code" maxlength="6" required>
      </label>
      <button type="submit">Verify</button>
    </form>
  </div>
</body>
</html>
"""


def create_admin_app(bot: TierAdminBot) -> Flask:
    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    configured_secret = os.getenv("TIER_BOT_APP_SECRET")
    if configured_secret:
        app.secret_key = configured_secret
    else:
        app.secret_key = secrets.token_hex(32)
        print("⚠️ TIER_BOT_APP_SECRET is not set. Login sessions will reset on each restart.")

    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    # Render is HTTPS-terminated, so secure cookies should be enabled by default.
    app.config["SESSION_COOKIE_SECURE"] = os.getenv("SESSION_COOKIE_SECURE", "1") == "1"
    app.config["PERMANENT_SESSION_LIFETIME"] = datetime.timedelta(days=30)

    def _is_authed() -> bool:
        return bool(session.get("authed") and session.get("uid"))

    def _resolve_content_and_vars(raw_content: str, template_name: str, vars_json: str) -> Tuple[str, Dict[str, Any]]:
        content = raw_content
        if template_name:
            tpl = bot.storage.get_template(template_name)
            if not tpl:
                raise ValueError("Template not found.")
            content = tpl["content"]

        variables: Dict[str, Any] = {}
        if vars_json.strip():
            parsed = json.loads(vars_json)
            if not isinstance(parsed, dict):
                raise ValueError("Template variables must be a JSON object.")
            variables = parsed
        return content, variables

    @app.route("/")
    def index():
        if not _is_authed():
            return redirect(url_for("login"))

        message = request.args.get("message")
        error = request.args.get("error")

        lookup_user_id = request.args.get("lookup_user_id")
        lookup_roles = []
        if lookup_user_id and lookup_user_id.isdigit():
            try:
                lookup_roles = asyncio.run_coroutine_threadsafe(
                    bot.list_member_roles(int(lookup_user_id)),
                    bot.loop,
                ).result(timeout=10)
            except Exception as exc:
                error = f"Role lookup failed: {exc}"

        channels = asyncio.run_coroutine_threadsafe(bot.list_text_channels(), bot.loop).result(timeout=10)
        templates = bot.storage.list_templates()
        schedules = bot.storage.list_schedules()
        scripts = bot.storage.list_scripts()
        return render_template_string(
            ADMIN_HTML,
            channels=channels,
            templates=templates,
            schedules=schedules,
            scripts=scripts,
            message=message,
            error=error,
            lookup_user_id=lookup_user_id,
            lookup_roles=lookup_roles,
            latency_ms=bot.get_latency_ms(),
            last_schedule_run_info=bot.last_schedule_run_info,
        )

    @app.route("/login")
    def login():
        if _is_authed():
            return redirect(url_for("index"))
        return render_template_string(LOGIN_HTML, error=request.args.get("error"), message=request.args.get("message"))

    @app.post("/start-login")
    def start_login():
        user_id = request.form.get("user_id", "").strip()
        if not user_id.isdigit():
            return redirect(url_for("login", error="User ID must be numeric."))
        uid = int(user_id)

        try:
            is_admin = asyncio.run_coroutine_threadsafe(bot.is_guild_admin(uid), bot.loop).result(timeout=10)
            if not is_admin:
                return redirect(url_for("login", error="User is not admin/manage_guild in target guild."))

            ok, msg = asyncio.run_coroutine_threadsafe(bot.issue_login_code(uid), bot.loop).result(timeout=15)
            if not ok:
                return redirect(url_for("login", error=msg))
            return redirect(url_for("login", message=msg))
        except Exception as exc:
            return redirect(url_for("login", error=f"Login init failed: {exc}"))

    @app.post("/verify-login")
    def verify_login():
        user_id = request.form.get("user_id", "").strip()
        code = request.form.get("code", "").strip()
        if not user_id.isdigit() or not code.isdigit() or len(code) != 6:
            return redirect(url_for("login", error="Provide numeric user ID and 6-digit code."))

        uid = int(user_id)
        if not bot.verify_login_code(uid, code):
            return redirect(url_for("login", error="Invalid or expired code."))

        session.permanent = True
        session["authed"] = True
        session["uid"] = uid
        return redirect(url_for("index", message="Login successful."))

    @app.get("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login", message="Logged out."))

    @app.post("/send-message")
    def send_message():
        if not _is_authed():
            return redirect(url_for("login"))
        channel_id = request.form.get("channel_id", "").strip()
        sender_mode = request.form.get("sender_mode", "me").strip().lower()
        custom_name = request.form.get("custom_name", "").strip()
        custom_avatar_url = request.form.get("custom_avatar_url", "").strip()
        template_name = request.form.get("template_name", "").strip()
        vars_json = request.form.get("vars_json", "").strip()
        raw_content = request.form.get("content", "").strip()
        if not channel_id.isdigit():
            return redirect(url_for("index", error="Channel is required."))
        if sender_mode not in {"me", "bot", "custom"}:
            return redirect(url_for("index", error="Invalid sender mode."))

        try:
            content, variables = _resolve_content_and_vars(raw_content, template_name, vars_json)
            if not content:
                return redirect(url_for("index", error="Message content resolved empty."))
            message = asyncio.run_coroutine_threadsafe(
                bot.relay_message(
                    int(channel_id),
                    content,
                    sender_mode,
                    int(session["uid"]),
                    custom_name,
                    custom_avatar_url,
                    variables,
                ),
                bot.loop,
            ).result(timeout=15)
            return redirect(url_for("index", message=message))
        except Exception as exc:
            return redirect(url_for("index", error=f"Send failed: {exc}"))

    @app.post("/save-template")
    def save_template():
        if not _is_authed():
            return redirect(url_for("login"))
        name = request.form.get("name", "").strip()
        content = request.form.get("content", "").strip()
        if not name or not content:
            return redirect(url_for("index", error="Template name and content are required."))
        bot.storage.save_template(name, content)
        return redirect(url_for("index", message=f"Template `{name}` saved."))

    @app.post("/delete-template")
    def delete_template():
        if not _is_authed():
            return redirect(url_for("login"))
        name = request.form.get("name", "").strip()
        if not name:
            return redirect(url_for("index", error="Template name required."))
        bot.storage.delete_template(name)
        return redirect(url_for("index", message=f"Template `{name}` deleted."))

    @app.post("/create-schedule")
    def create_schedule():
        if not _is_authed():
            return redirect(url_for("login"))

        mode = request.form.get("mode", "").strip()
        channel_id = request.form.get("channel_id", "").strip()
        sender_mode = request.form.get("sender_mode", "bot").strip().lower()
        delay_minutes = request.form.get("delay_minutes", "5").strip()
        daily_time_utc = request.form.get("daily_time_utc", "").strip()
        fixed_datetime_utc = request.form.get("fixed_datetime_utc", "").strip()
        template_name = request.form.get("template_name", "").strip()
        vars_json = request.form.get("vars_json", "").strip()
        custom_name = request.form.get("custom_name", "").strip()
        custom_avatar_url = request.form.get("custom_avatar_url", "").strip()
        raw_content = request.form.get("content", "").strip()

        if mode not in {"once", "daily", "fixed_date"}:
            return redirect(url_for("index", error="Invalid schedule mode."))
        if not channel_id.isdigit():
            return redirect(url_for("index", error="Channel is required."))
        if sender_mode not in {"me", "bot", "custom"}:
            return redirect(url_for("index", error="Invalid sender mode."))

        try:
            content, variables = _resolve_content_and_vars(raw_content, template_name, vars_json)
            if not content:
                return redirect(url_for("index", error="Schedule content resolved empty."))

            run_at_utc = None
            daily_hhmm = None
            now = datetime.datetime.utcnow()

            if mode == "once":
                mins = int(delay_minutes)
                if mins < 1:
                    mins = 1
                run_at_utc = (now + datetime.timedelta(minutes=mins)).isoformat()
            elif mode == "daily":
                if not re.fullmatch(r"^\d{2}:\d{2}$", daily_time_utc):
                    return redirect(url_for("index", error="Daily time must be HH:MM (UTC)."))
                daily_hhmm = daily_time_utc
            else:
                dt = datetime.datetime.strptime(fixed_datetime_utc, "%Y-%m-%d %H:%M")
                run_at_utc = dt.isoformat()

            schedule_id = bot.storage.create_schedule(
                mode=mode,
                channel_id=int(channel_id),
                sender_mode=sender_mode,
                content=content,
                custom_name=custom_name,
                custom_avatar_url=custom_avatar_url,
                run_at_utc=run_at_utc,
                daily_time_utc=daily_hhmm,
                vars_json=json.dumps(variables),
            )
            return redirect(url_for("index", message=f"Schedule #{schedule_id} created."))
        except Exception as exc:
            return redirect(url_for("index", error=f"Create schedule failed: {exc}"))

    @app.post("/delete-schedule")
    def delete_schedule():
        if not _is_authed():
            return redirect(url_for("login"))
        schedule_id = request.form.get("schedule_id", "").strip()
        if not schedule_id.isdigit():
            return redirect(url_for("index", error="Invalid schedule ID."))
        bot.storage.delete_schedule(int(schedule_id))
        return redirect(url_for("index", message=f"Schedule #{schedule_id} deleted."))

    @app.post("/assign-role")
    def assign_role():
        if not _is_authed():
            return redirect(url_for("login"))
        user_id = request.form.get("user_id", "").strip()
        role_name = request.form.get("role_name", "").strip()
        color_hex = request.form.get("color_hex", "").strip()

        if not user_id.isdigit() or not role_name or not color_hex:
            return redirect(url_for("index", error="User ID, role name, and color are required."))

        try:
            msg = asyncio.run_coroutine_threadsafe(
                bot.assign_custom_role(int(user_id), role_name, color_hex),
                bot.loop,
            ).result(timeout=20)
            return redirect(url_for("index", message=msg))
        except Exception as exc:
            return redirect(url_for("index", error=f"Role assign failed: {exc}"))

    @app.post("/remove-roles")
    def remove_roles():
        if not _is_authed():
            return redirect(url_for("login"))
        user_id = request.form.get("user_id", "").strip()
        role_ids = request.form.getlist("role_ids")
        if not user_id.isdigit():
            return redirect(url_for("index", error="User ID is required."))

        parsed_ids = [int(rid) for rid in role_ids if rid.isdigit()]
        try:
            msg = asyncio.run_coroutine_threadsafe(
                bot.remove_member_roles(int(user_id), parsed_ids),
                bot.loop,
            ).result(timeout=20)
            return redirect(url_for("index", message=msg, lookup_user_id=user_id))
        except Exception as exc:
            return redirect(url_for("index", error=f"Role removal failed: {exc}", lookup_user_id=user_id))

    @app.post("/equip-badge")
    def equip_badge():
        if not _is_authed():
            return redirect(url_for("login"))
        user_id = request.form.get("user_id", "").strip()
        badge_name = request.form.get("badge_name", "").strip()
        if not user_id.isdigit() or not badge_name:
            return redirect(url_for("index", error="User ID and badge name are required."))

        try:
            msg = asyncio.run_coroutine_threadsafe(
                bot.equip_badge(int(user_id), badge_name),
                bot.loop,
            ).result(timeout=20)
            return redirect(url_for("index", message=msg))
        except Exception as exc:
            return redirect(url_for("index", error=f"Badge update failed: {exc}"))

    @app.post("/bulk-badge")
    def bulk_badge():
        if not _is_authed():
            return redirect(url_for("login"))
        badge_name = request.form.get("badge_name", "").strip()
        user_ids_text = request.form.get("user_ids_text", "").strip()
        if not badge_name or not user_ids_text:
            return redirect(url_for("index", error="Badge name and user list are required."))

        ids: List[int] = []
        for line in user_ids_text.splitlines():
            value = line.strip()
            if value.isdigit():
                ids.append(int(value))
        if not ids:
            return redirect(url_for("index", error="No valid numeric user IDs found."))

        try:
            res = asyncio.run_coroutine_threadsafe(
                bot.bulk_equip_badge(ids, badge_name),
                bot.loop,
            ).result(timeout=240)
            msg = f"Bulk badge done. success={res['success']} failed={res['failed']}"
            if res["failed_user_ids"]:
                msg += f" failed_ids={res['failed_user_ids'][:20]}"
            return redirect(url_for("index", message=msg))
        except Exception as exc:
            return redirect(url_for("index", error=f"Bulk badge failed: {exc}"))

    @app.post("/save-script")
    def save_script():
        if not _is_authed():
            return redirect(url_for("login"))
        name = request.form.get("name", "").strip()
        code = request.form.get("code", "").strip()
        if not name or not code:
            return redirect(url_for("index", error="Script name and code are required."))
        bot.storage.save_script(name, code)
        return redirect(url_for("index", message=f"Script `{name}` saved."))

    @app.post("/delete-script")
    def delete_script():
        if not _is_authed():
            return redirect(url_for("login"))
        name = request.form.get("name", "").strip()
        if not name:
            return redirect(url_for("index", error="Script name required."))
        bot.storage.delete_script(name)
        return redirect(url_for("index", message=f"Script `{name}` deleted."))

    @app.post("/run-script")
    def run_script():
        if not _is_authed():
            return redirect(url_for("login"))
        script_name = request.form.get("script_name", "").strip()
        args_json = request.form.get("args_json", "").strip()
        if not script_name:
            return redirect(url_for("index", error="Script name required."))
        try:
            args = {}
            if args_json:
                args = json.loads(args_json)
                if not isinstance(args, dict):
                    return redirect(url_for("index", error="Args JSON must be an object."))

            output = asyncio.run_coroutine_threadsafe(
                bot.run_admin_script(script_name, args),
                bot.loop,
            ).result(timeout=240)
            return redirect(url_for("index", message=f"Script `{script_name}` executed: {output}"))
        except Exception as exc:
            return redirect(url_for("index", error=f"Run script failed: {exc}"))

    @app.post("/run-sync")
    def run_sync():
        if not _is_authed():
            return redirect(url_for("login"))
        try:
            res = asyncio.run_coroutine_threadsafe(bot.run_tier_sync_once(), bot.loop).result(timeout=180)
            message = (
                f"Sync done: scanned={res['members_scanned']} "
                f"tier_changes={res['changes']} tier_unchanged={res['unchanged']} "
                f"level_changes={res['level_changes']} level_unchanged={res['level_unchanged']}"
            )
            return redirect(url_for("index", message=message))
        except Exception as exc:
            return redirect(url_for("index", error=f"Manual sync failed: {exc}"))

    @app.get("/health")
    def health():
        ready = bot.is_ready() if not bot.is_closed() else False
        return {"status": "ok", "ready": ready}, (200 if ready else 503)

    return app


def run_waitress(app: Flask):
    from waitress import serve

    # Render usually provides PORT; keep 10000 as a practical local fallback.
    port = _env_int("PORT", 10000) or 10000
    print(f"🌍 Tier admin web server listening on {port}")
    serve(app, host="0.0.0.0", port=port, _quiet=False)


def run_tier_admin_bot():
    token = os.getenv("TIER_BOT_TOKEN") or os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("TIER_BOT_TOKEN (or DISCORD_TOKEN fallback) is required.")

    bot = TierAdminBot()
    app = create_admin_app(bot)

    def handle_signal(signum, frame):
        if not bot.is_closed():
            asyncio.run_coroutine_threadsafe(bot.close(), bot.loop)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    web_thread = threading.Thread(target=run_waitress, args=(app,), daemon=True)
    web_thread.start()

    bot.run(token)
