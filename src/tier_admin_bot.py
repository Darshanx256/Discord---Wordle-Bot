import asyncio
import datetime
import os
import random
import secrets
import signal
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

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
        self.allowed_mentions = discord.AllowedMentions(
            everyone=False, roles=True, users=True, replied_user=False
        )

        self.supabase_client: Optional[Client] = None
        self.pending_login_codes: Dict[int, Tuple[str, float]] = {}
        self._background_tasks: Dict[str, asyncio.Task] = {}
        self._channel_cache: Tuple[float, List[discord.TextChannel]] = (0.0, [])
        self._webhook_cache: Dict[int, int] = {}

    async def setup_hook(self):
        if not self.guild_id:
            raise RuntimeError("TIER_BOT_GUILD_ID is required.")
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise RuntimeError("SUPABASE_URL and SUPABASE_KEY are required.")

        self.supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
        self._background_tasks["hourly_sync"] = asyncio.create_task(self.hourly_tier_sync_loop())
        print("✅ TierAdminBot setup complete.")

    async def close(self):
        for task in self._background_tasks.values():
            task.cancel()
        await super().close()

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

    async def run_tier_sync_once(self) -> dict:
        guild = await self.get_target_guild()
        if not guild.chunked:
            await guild.chunk(cache=True)
        tier_roles = await self._ensure_tier_roles(guild)
        tier_role_ids = {r.id for r in tier_roles.values()}

        members = [m for m in guild.members if not m.bot]
        member_ids = [m.id for m in members]
        wr_map = await self._fetch_member_wr_map(member_ids)

        changes = 0
        skipped = 0
        for member in members:
            wr = wr_map.get(member.id, 0)
            tier_name = tier_from_wr(wr)
            target_role = tier_roles.get(tier_name) if tier_name else None
            current_tier_roles = [r for r in member.roles if r.id in tier_role_ids]

            has_target = target_role is not None and target_role in current_tier_roles
            only_target = has_target and len(current_tier_roles) == 1
            if only_target:
                skipped += 1
                continue

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

            await asyncio.sleep(max(0.2, self.member_update_delay))

        return {
            "members_scanned": len(members),
            "db_rows": len(wr_map),
            "changes": changes,
            "unchanged": skipped,
        }

    async def hourly_tier_sync_loop(self):
        await self.wait_until_ready()
        while not self.is_closed():
            started = datetime.datetime.utcnow()
            try:
                result = await self.run_tier_sync_once()
                print(
                    "✅ Tier sync done",
                    f"at={started.isoformat()}",
                    f"scanned={result['members_scanned']}",
                    f"changes={result['changes']}",
                    f"unchanged={result['unchanged']}",
                )
            except Exception as exc:
                print(f"❌ Tier sync error: {exc}")

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

    async def relay_message(self, channel_id: int, content: str, sender_user_id: int) -> str:
        guild = await self.get_target_guild()
        channel = guild.get_channel(channel_id)
        if channel is None or not isinstance(channel, discord.TextChannel):
            raise ValueError("Invalid text channel.")

        member = guild.get_member(sender_user_id)
        if member is None:
            try:
                member = await guild.fetch_member(sender_user_id)
            except discord.HTTPException:
                member = None

        sender_name = member.display_name if member else "Relay"
        sender_avatar = member.display_avatar.url if member else None

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

        await webhook.send(
            content=content,
            username=sender_name,
            avatar_url=sender_avatar,
            allowed_mentions=self.allowed_mentions,
            wait=True,
        )
        return f"Message relayed to #{channel.name} as {sender_name}."

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
    button { cursor: pointer; }
    .ok { color: #0a7a28; }
    .err { color: #b3261e; }
    .grid { display: grid; grid-template-columns: 1fr; gap: 14px; }
    @media (min-width: 900px) { .grid { grid-template-columns: 1fr 1fr; } }
  </style>
</head>
<body>
  <h2>Tier Admin Panel</h2>
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
        <label>Content
          <textarea name="content" rows="5" required></textarea>
        </label>
        <button type="submit">Send</button>
      </form>
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
        return render_template_string(
            ADMIN_HTML,
            channels=channels,
            message=message,
            error=error,
            lookup_user_id=lookup_user_id,
            lookup_roles=lookup_roles,
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
        content = request.form.get("content", "").strip()
        if not channel_id.isdigit() or not content:
            return redirect(url_for("index", error="Channel and content are required."))

        try:
            message = asyncio.run_coroutine_threadsafe(
                bot.relay_message(int(channel_id), content, int(session["uid"])),
                bot.loop,
            ).result(timeout=15)
            return redirect(url_for("index", message=message))
        except Exception as exc:
            return redirect(url_for("index", error=f"Send failed: {exc}"))

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

    @app.post("/run-sync")
    def run_sync():
        if not _is_authed():
            return redirect(url_for("login"))
        try:
            res = asyncio.run_coroutine_threadsafe(bot.run_tier_sync_once(), bot.loop).result(timeout=120)
            message = f"Sync done: scanned={res['members_scanned']} changes={res['changes']} unchanged={res['unchanged']}"
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
