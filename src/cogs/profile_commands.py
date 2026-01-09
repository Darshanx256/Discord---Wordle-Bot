"""
Profile commands cog: /profile command
"""
import discord
from discord.ext import commands
from src.database import fetch_user_profile_v2
from src.utils import get_badge_full_display


class ProfileCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="profile", description="Check your personal V2 stats.")
    async def profile(self, ctx):
        await ctx.defer()

        p = fetch_user_profile_v2(self.bot, ctx.author.id)
        if not p:
            return await ctx.send("You haven't played directly yet!", ephemeral=True)

        embed = discord.Embed(color=discord.Color.teal())
        embed.set_author(name=f"{ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)

        badge_str = ""
        if p.get('active_badge'):
            badge_full = get_badge_full_display(p['active_badge'])
            badge_str = f"Permission Badge: {badge_full}\n" if badge_full else ""

        tier = p.get('tier', {})
        tier_name = tier.get('name', 'Unranked')
        tier_icon = tier.get('icon', '')

        embed.description = f"{badge_str}**Level {p.get('level', 1)}** | {tier_icon} **{tier_name}**"

        embed.add_field(name="⚔️ Multiplayer", value=f"WR: **{p['multi_wr']}**\nWins: {p['multi_wins']}", inline=True)
        embed.add_field(name="🕵️ Solo", value=f"WR: **{p['solo_wr']}**\nWins: {p['solo_wins']}", inline=True)
        
        # --- STREAK DISPLAY ---
        #from src.utils import EMOJIS
        #curr_streak = p.get('current_streak', 0)
        #max_streak = p.get('max_streak', 0)
        #streak_emoji = EMOJIS.get('fire', '🔥') if curr_streak > 0 else '❄️'
        #streak_val = f"{streak_emoji} Current: **{curr_streak}**\n🏆 Highest: **{max_streak}**"
        #embed.add_field(name="🔥 Streaks", value=streak_val, inline=True)

        eggs = p.get('eggs', {})
        egg_str = "None"
        if eggs:
            egg_str = "\n".join([f"{k.capitalize()}: {v}x" for k, v in eggs.items()])

        embed.add_field(name="🎒 Collection", value=egg_str, inline=False)

        curr = p.get('current_level_xp', 0)
        nxt = p.get('next_level_xp', 100)
        pct = min(1.0, curr / nxt)
        bar_len = 10
        filled_len = int(bar_len * pct)
        bar = "█" * filled_len + "░" * (bar_len - filled_len)

        embed.add_field(name="Level Progress", value=f"`{bar}` {curr}/{nxt} XP", inline=False)

        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(ProfileCommands(bot))
