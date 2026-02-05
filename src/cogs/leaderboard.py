"""
Leaderboard commands cog: /leaderboard, /leaderboard_global, and helpers
"""
import asyncio
import discord
from discord.ext import commands
from discord.ext import commands
from src.config import TIERS
from src.ui_v2 import LeaderboardViewV2
from src.utils import EMOJIS, get_cached_username
import datetime


async def fetch_and_format_rankings(results, bot_instance, guild=None):
    """
    Fetch and format ranking data with user names and tier icons.
    Minimizes API calls with concurrency and caching.
    """
    # Optimized: No more semaphores needed. 'get_cached_username' is now non-blocking (mentions).
    
    tasks = []
    
    async def process_row(i, row_data):
         # Unpack flexibly: (uid, wins, xp, wr, badge)
         uid, w, xp, wr, badge = row_data
         
         # 1. Get Name (Cache/Mention)
         # Using the new safe util - no API spam
         name = await get_cached_username(bot_instance, uid)
                 
         # 2. Get Tier Icon
         tier_icon = "🛡️"
         for t in TIERS:
             if wr >= t['min_wr']:
                 tier_icon = EMOJIS.get(t['icon'], t['icon'])
                 break
                 
         return (i + 1, name, w, xp, wr, tier_icon, badge)

    return await asyncio.gather(*(process_row(i, r) for i, r in enumerate(results)))


class LeaderboardCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.global_cache = None
        self.global_cache_time = None


    @commands.hybrid_command(name="leaderboard", description="Server Leaderboard (Multiplayer WR).")
    async def leaderboard(self, ctx):
        if not ctx.guild:
            return await ctx.send("Server leaderboard only available in servers. Use `/leaderboard_global` for global rankings.", ephemeral=True)
        await ctx.defer()

        try:
            # Step 1: Get User IDs in this guild
            g_response = self.bot.supabase_client.table('guild_stats_v2') \
                .select('user_id') \
                .eq('guild_id', ctx.guild.id) \
                .execute()

            if not g_response.data:
                return await ctx.send("No ranked players in this server yet!", ephemeral=True)

            guild_user_ids = [r['user_id'] for r in g_response.data]

            # Step 2: Fetch Stats for these users
            u_response = self.bot.supabase_client.table('user_stats_v2') \
                .select('user_id, multi_wins, xp, multi_wr, active_badge') \
                .in_('user_id', guild_user_ids) \
                .execute()

            results = []
            for r in u_response.data:
                results.append((r['user_id'], r['multi_wins'], r['xp'], r['multi_wr'], r.get('active_badge')))

            # Sort by WR desc and slice Top 10
            results.sort(key=lambda x: x[3], reverse=True)
            results = results[:10]

        except Exception as e:
            print(f"Leaderboard Error: {e}")
            return await ctx.send("❌ Error fetching leaderboard. Please try again later.", ephemeral=True)

        if not results:
            return await ctx.send("No ranked players yet!", ephemeral=True)

        data = await fetch_and_format_rankings(results, self.bot, ctx.guild)

        view = LeaderboardViewV2(self.bot, data, f"🏆 {ctx.guild.name} Top 10", discord.Color.gold(), ctx.author)
        await ctx.send(embed=view.create_embed(), view=view)

    @commands.hybrid_command(name="leaderboard_global", description="Global Leaderboard (Multiplayer WR).")
    async def leaderboard_global(self, ctx):
        await ctx.defer()
        
        # 1. OPTIMIZATION: Check Cache (1 minute TTL)
        if self.global_cache and self.global_cache_time:
             if (datetime.datetime.utcnow() - self.global_cache_time).total_seconds() < 60:
                 # Verify cache isn't empty
                 results, total_count = self.global_cache
                 if results:
                    data = await fetch_and_format_rankings(results, self.bot)
                    view = LeaderboardViewV2(self.bot, data, "🌍 Global Top 10", discord.Color.purple(), ctx.author)
                    return await ctx.send(embed=view.create_embed(), view=view)

        try:
             # OPTIMIZATION: Fetch Total Count Efficiently
             # Use a simple count query. explicit select of 1 column + limit 1 is safest.
            count_res = self.bot.supabase_client.table('user_stats_v2') \
                .select('user_id', count='exact') \
                .limit(1) \
                .execute()
            total_count = count_res.count if count_res.count is not None else 0

            response = self.bot.supabase_client.table('user_stats_v2') \
                .select('user_id, multi_wins, xp, multi_wr, active_badge') \
                .order('multi_wr', desc=True) \
                .limit(10) \
                .execute()

            if not response.data:
                return await ctx.send("No records found in global leaderboard.", ephemeral=True)

            results = [(r['user_id'], r['multi_wins'], r['xp'], r['multi_wr'], r.get('active_badge')) for r in response.data]
            
            # Save to Cache
            self.global_cache = (results, total_count)
            # Use timezone-aware UTC
            self.global_cache_time = datetime.datetime.now(datetime.timezone.utc)

        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"Global Leaderboard Error: {e}")
            return await ctx.send(f"❌ Error fetching global leaderboard: {e}", ephemeral=True)

        if not results:
            return await ctx.send("No players yet!", ephemeral=True)

        data = await fetch_and_format_rankings(results, self.bot)

        view = LeaderboardViewV2(self.bot, data, "🌍 Global Top 10", discord.Color.purple(), ctx.author)
        await ctx.send(embed=view.create_embed(), view=view)


async def setup(bot):
    await bot.add_cog(LeaderboardCommands(bot))
