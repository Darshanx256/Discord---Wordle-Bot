"""
Help command cog: /help
"""
import discord
from discord.ext import commands
from src.ui import HelpView


class HelpCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="help", description="How to play and command guide.")
    async def help_cmd(self, ctx):
        view = HelpView(ctx.author)
        await ctx.send(embed=view.create_embed(), view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(HelpCommands(bot))
