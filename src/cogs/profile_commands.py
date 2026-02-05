"""
Profile commands cog: /profile command
"""
import discord
from discord.ext import commands
from src.database import fetch_user_profile_v2
from src.ui_v2 import ProfileViewV2


class ProfileCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="profile", description="Check your personal V2 stats.")
    async def profile(self, ctx):
        await ctx.defer()

        p = await fetch_user_profile_v2(self.bot, ctx.author.id)
        if not p:
            return await ctx.send("You haven't played directly yet!", ephemeral=True)

        view = ProfileViewV2(self.bot, p, ctx.author)
        await ctx.send(embed=view.create_embed(), view=view)


async def setup(bot):
    await bot.add_cog(ProfileCommands(bot))
