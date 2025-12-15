"""
Help command cog: /help
"""
import discord
from discord.ext import commands
from discord import app_commands
from src.ui import HelpView


class HelpCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="help", description="How to play and command guide.")
    async def help_cmd(self, interaction: discord.Interaction):
        view = HelpView(interaction.user)
        await interaction.response.send_message(embed=view.create_embed(), view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(HelpCommands(bot))
