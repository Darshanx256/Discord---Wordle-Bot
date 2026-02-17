"""
Leaderboard commands cog: /leaderboard, /leaderboard_global, and helpers
"""
import asyncio
import discord
from discord.ext import commands
from src.config import TIERS
from src.ui import LeaderboardView
from src.utils import EMOJIS
import datetime


async def fetch_and_format_rankings(results, bot_instance, guild=None):
    """
    Fetch and format ranking data with user names and tier icons.
    Minimizes API calls with concurrency and caching.
    """
    sem = asyncio.Semaphore(5)

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
        self.global_cache = None
        self.global_cache_time = None


    @commands.hybrid_command(name="leaderboard", description="Server Leaderboard (Multiplayer WR).")
    async def leaderboard(self, ctx):
        if not ctx.guild:
            return
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

            # Sort by WR desc
            results.sort(key=lambda x: x[3], reverse=True)
            results = results[:50]

        except Exception as e:
            print(f"Leaderboard Error: {e}")
            return await ctx.send("❌ Error fetching leaderboard. Please try again later.", ephemeral=True)

        if not results:
            return await ctx.send("No ranked players yet!", ephemeral=True)

        data = await fetch_and_format_rankings(results, self.bot, ctx.guild)

        view = LeaderboardView(self.bot, data, f"🏆 {ctx.guild.name} Leaderboard", discord.Color.gold(), ctx.author)
        await ctx.send(embed=view.create_embed(), view=view)

    @commands.hybrid_command(name="leaderboard_global", description="Global Leaderboard (Multiplayer WR).")
    async def leaderboard_global(self, ctx):
        await ctx.defer()

        def build_global_top10_embed(results):
            embed = discord.Embed(title="🌍 Global Top 10", color=discord.Color.purple())
            lines = []
            for i, row in enumerate(results, start=1):
                uid, wins, xp, wr, badge = row
                tier_icon = "🛡️"
                for t in TIERS:
                    if wr >= t['min_wr']:
                        tier_icon = EMOJIS.get(t['icon'], t['icon'])
                        break
                badge_icon = f" {EMOJIS.get(badge, '')}" if badge else ""
                lines.append(
                    f"`#{i:02d}` {tier_icon} <@{uid}>{badge_icon} • WR `{wr}` • W `{wins}` • XP `{xp}`"
                )
            embed.description = "\n".join(lines) if lines else "No records found."
            embed.set_footer(text="Cached for 60s")
            return embed

        # Cache check (60s TTL)
        if self.global_cache and self.global_cache_time:
            if (datetime.datetime.utcnow() - self.global_cache_time).total_seconds() < 60:
                cached_results = self.global_cache
                if cached_results:
                    return await ctx.send(
                        embed=build_global_top10_embed(cached_results),
                        allowed_mentions=discord.AllowedMentions.none()
                    )

        try:
            # Fetch only top 10 for faster response and cleaner output.
            response = self.bot.supabase_client.table('user_stats_v2') \
                .select('user_id, multi_wins, xp, multi_wr, active_badge') \
                .order('multi_wr', desc=True) \
                .limit(10) \
                .execute()

            if not response.data:
                return await ctx.send("No records found in global leaderboard.", ephemeral=True)

            results = [(r['user_id'], r['multi_wins'], r['xp'], r['multi_wr'], r.get('active_badge')) for r in response.data]

            # Save to cache
            self.global_cache = results
            self.global_cache_time = datetime.datetime.utcnow()

        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"Global Leaderboard Error: {e}")
            return await ctx.send(f"❌ Error fetching global leaderboard: {e}", ephemeral=True)

        if not results:
            return await ctx.send("No players yet!", ephemeral=True)

        await ctx.send(
            embed=build_global_top10_embed(results),
            allowed_mentions=discord.AllowedMentions.none()
        )


async def setup(bot):
    await bot.add_cog(LeaderboardCommands(bot))
