"""
Leaderboard commands cog: /leaderboard, /leaderboard_global, and helpers
"""
import asyncio
import datetime
import discord
from discord.ext import commands, tasks
from src.config import TIERS
from src.ui_v2 import LeaderboardViewV2
from src.utils import EMOJIS, get_cached_username


GLOBAL_CACHE_TTL_SECONDS = 5 * 60


async def fetch_and_format_rankings(results, bot_instance, guild=None, *, allow_cache_write: bool = True, force_mentions: bool = False):
    """
    Fetch and format ranking data with user names and tier icons.
    Minimizes API calls with concurrency and caching.
    """
    # Optimized: No more semaphores needed. 'get_cached_username' is now non-blocking (mentions).
    
    async def process_row(i, row_data):
        # Unpack flexibly: (uid, wins, xp, wr, badge)
        uid, w, xp, wr, badge = row_data

        # 1. Get Name (Cache/Mention)
        # Using the new safe util - no API spam
        if force_mentions:
            name = f"<@{uid}>"
        else:
            name = await get_cached_username(bot_instance, uid, allow_cache_write=allow_cache_write)

        # 2. Get Tier Icon
        tier_icon = "🛡️"
        for t in TIERS:
            if wr >= t['min_wr']:
                tier_icon = EMOJIS.get(t['icon'], t['icon'])
                break

        return (i + 1, name, w, xp, wr, tier_icon, badge)

    async def fetch_user_safe(row_data):
        i, (uid, w, xp, wr, badge) = row_data
        name = f"User {uid}"

        # Determine Tier Icon based on WR
        tier_icon = "🛡️"
        for t in TIERS:
            if wr >= t['min_wr']:
                # FIX: Use EMOJIS.get to handle custom emojis like 'legend_tier'
                tier_icon = EMOJIS.get(t['icon'], t['icon'])
                break

        # 1. Try Local Cache (FAST & SAFE)
        if guild:
            member = guild.get_member(uid)
            if member:
                return (i + 1, member.display_name, w, xp, wr, tier_icon, badge)

        # 2. Try Global Bot Cache (FAST & SAFE)
        user = bot_instance.get_user(uid)
        if user:
            return (i + 1, user.display_name, w, xp, wr, tier_icon, badge)

        # 3. Try In-Memory Name Cache (FAST)
        if uid in bot_instance.name_cache:
            return (i + 1, bot_instance.name_cache[uid], w, xp, wr, tier_icon, badge)

        # 4. API Call (SLOW - Needs Semaphore)
        async with sem:
            try:
                u = await bot_instance.fetch_user(uid)
                name = u.display_name
                bot_instance.name_cache[uid] = name
            except:
                pass

        return (i + 1, name, w, xp, wr, tier_icon, badge)

    tasks = [fetch_user_safe((i, r)) for i, r in enumerate(results)]
    formatted_data = await asyncio.gather(*tasks)
    return formatted_data


class LeaderboardCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.global_cache = []
        self.global_cache_time = None

    async def cog_load(self):
        # Seed once at startup, then continue on interval.
        await self._refresh_global_cache()
        if not self._global_cache_loop.is_running():
            self._global_cache_loop.start()

    async def cog_unload(self):
        if self._global_cache_loop.is_running():
            self._global_cache_loop.cancel()

    @tasks.loop(seconds=GLOBAL_CACHE_TTL_SECONDS)
    async def _global_cache_loop(self):
        await self._refresh_global_cache()

    @_global_cache_loop.before_loop
    async def _before_global_cache_loop(self):
        await self.bot.wait_until_ready()
        await self._refresh_global_cache()

    async def _refresh_global_cache(self):
        try:
            response = await asyncio.to_thread(
                lambda: self.bot.supabase_client.table('user_stats_v2')
                .select('user_id, multi_wins, xp, multi_wr, active_badge')
                .order('multi_wr', desc=True)
                .limit(10)
                .execute()
            )
            if response.data:
                self.global_cache = [
                    (r['user_id'], r['multi_wins'], r['xp'], r['multi_wr'], r.get('active_badge'))
                    for r in response.data
                ]
                self.global_cache_time = datetime.datetime.now(datetime.timezone.utc)
        except Exception as e:
            print(f"Global Cache Refresh Error: {e}")

    async def _fetch_user_rank_and_total(self, user_id: int):
        """
        Single-query rank + total via RPC.
        Falls back to multi-query if RPC is unavailable.
        """
        try:
            res = await asyncio.to_thread(
                lambda: self.bot.supabase_client.rpc('get_global_rank_v1', {'p_user_id': user_id}).execute()
            )
            if res.data:
                row = res.data[0]
                return row.get('user_rank'), row.get('total_players')
        except Exception as e:
            print(f"Global Rank RPC Error: {e}")

        # If user is not ranked, still return total players.
        def _count_total_only():
            total_res = self.bot.supabase_client.table('user_stats_v2') \
                .select('user_id', count='exact') \
                .limit(1) \
                .execute()
            total = total_res.count if total_res.count is not None else 0
            return None, total

        try:
            return await asyncio.to_thread(_count_total_only)
        except Exception as e:
            print(f"Global Rank Count Error: {e}")

        # Fallback (multi-query) if RPC missing or fails
        def _fallback():
            user_res = self.bot.supabase_client.table('user_stats_v2') \
                .select('multi_wr') \
                .eq('user_id', user_id) \
                .limit(1) \
                .execute()
            if not user_res.data:
                return None, None
            user_wr = user_res.data[0].get('multi_wr', 0)

            above_res = self.bot.supabase_client.table('user_stats_v2') \
                .select('user_id', count='exact') \
                .gt('multi_wr', user_wr) \
                .execute()
            above = above_res.count if above_res.count is not None else 0

            total_res = self.bot.supabase_client.table('user_stats_v2') \
                .select('user_id', count='exact') \
                .limit(1) \
                .execute()
            total = total_res.count if total_res.count is not None else 0

            return above + 1, total

        return await asyncio.to_thread(_fallback)



    @commands.hybrid_command(name="leaderboard", description="Server Leaderboard (Multiplayer WR).")
    async def leaderboard(self, ctx):
        if not ctx.guild:
            return
        await ctx.defer()

        try:
            # Step 1: Get User IDs in this guild
            g_response = await asyncio.to_thread(
                lambda: self.bot.supabase_client.table('guild_stats_v2')
                .select('user_id')
                .eq('guild_id', ctx.guild.id)
                .execute()
            )

            if not g_response.data:
                return await ctx.send("No ranked players in this server yet!", ephemeral=True)

            guild_user_ids = [r['user_id'] for r in g_response.data]

            # Step 2: Fetch Stats for these users
            u_response = await asyncio.to_thread(
                lambda: self.bot.supabase_client.table('user_stats_v2')
                .select('user_id, multi_wins, xp, multi_wr, active_badge')
                .in_('user_id', guild_user_ids)
                .execute()
            )

            results = []
            for r in u_response.data:
                results.append((r['user_id'], r['multi_wins'], r['xp'], r['multi_wr'], r.get('active_badge')))

            # Sort by WR desc
            results.sort(key=lambda x: x[3], reverse=True)
            results = results[:50]

        except Exception as e:
            print(f"Leaderboard Error: {e}")
            return await ctx.send("❌ Error fetching leaderboard. Please try again later.", ephemeral=True)

        if not results:
            return await ctx.send("No ranked players yet!", ephemeral=True)

        data = await fetch_and_format_rankings(results, self.bot, ctx.guild, allow_cache_write=False, force_mentions=True)

        view = LeaderboardView(self.bot, data, f"🏆 {ctx.guild.name} Leaderboard", discord.Color.gold(), ctx.author)
        await ctx.send(embed=view.create_embed(), view=view)

    @commands.hybrid_command(name="leaderboard_global", description="Global Leaderboard (Multiplayer WR).")
    async def leaderboard_global(self, ctx):
        await ctx.defer()

        if not self.global_cache:
            return await ctx.send("Global leaderboard cache is warming up. Please try again in a minute.", ephemeral=True)

        data = await fetch_and_format_rankings(self.global_cache, self.bot, allow_cache_write=False)

        view = LeaderboardViewV2(
            self.bot,
            data,
            "🌍 Global Top 10",
            discord.Color.purple(),
            ctx.author,
            footer_text="Fetching rank..."
        )
        msg = await ctx.send(embed=view.create_embed(), view=view)

        # Fetch rank + total players in the background (single-query RPC)
        user_rank, total_players = await self._fetch_user_rank_and_total(ctx.author.id)
        if user_rank and total_players:
            view.footer_text = f"You Rank #{user_rank}/{total_players} total players"
        elif total_players is not None:
            view.footer_text = f"Total players: {total_players}"
        else:
            view.footer_text = "Rank unavailable"

        await msg.edit(embed=view.create_embed(), view=view)


async def setup(bot):
    await bot.add_cog(LeaderboardCommands(bot))
