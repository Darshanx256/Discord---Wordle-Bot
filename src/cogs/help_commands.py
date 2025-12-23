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

    # ============================================================
    # /credits command - PLACEHOLDER (uncomment when ready to use)
    # ============================================================
    # @app_commands.command(name="credits", description="View bot credits and acknowledgements.")
    # async def credits_cmd(self, interaction: discord.Interaction):
    #     embed = discord.Embed(
    #         title="🎬 Credits & Acknowledgements",
    #         color=discord.Color.gold()
    #     )
    #     embed.description = "Thank you to everyone who made this bot possible!"
    #     
    #     embed.add_field(
    #         name="👨‍💻 Developer",
    #         value="• **[Developer Name]** — Creator & Maintainer",
    #         inline=False
    #     )
    #     
    #     embed.add_field(
    #         name="🎨 Assets & Icons",
    #         value=(
    #             "• **[Icon Credit]** — Bot Icon\\n"
    #             "• **[Emoji Credit]** — Custom Emojis"
    #         ),
    #         inline=False
    #     )
    #     
    #     embed.add_field(
    #         name="🙏 Special Thanks",
    #         value=(
    #             "• **[Person/Project]** — For inspiration\\n"
    #             "• **[Person/Project]** — For support"
    #         ),
    #         inline=False
    #     )
    #     
    #     embed.add_field(
    #         name="📚 Libraries",
    #         value=(
    #             "• **discord.py** — Discord API Wrapper\\n"
    #             "• **Supabase** — Database"
    #         ),
    #         inline=False
    #     )
    #     
    #     embed.set_footer(text="Made with ❤️")
    #     await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(HelpCommands(bot))
