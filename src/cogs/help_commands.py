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
    # /credits command
    # ============================================================
    @app_commands.command(name="credits", description="View bot credits and acknowledgements.")
    async def credits_cmd(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🎬 Credits & Acknowledgements",
            color=discord.Color.gold()
        )
        embed.description = "Happy New Year! Here's all the resource I used to make this bot possible."
        
        embed.add_field(
            name="👨‍💻 Contact Developer",
            value=(
                "• **Oretsu** ->  Creator & Maintainer\n"
                "• Contact me via /message or email at - `Ortsx256@proton.me`"
            ),
            inline=False
        )

        embed.add_field(
            name="🎨 Assets & Icons",
            value=(
                "• **[Icon Credit]** -> Octopus Icon by Whitevector - Flaticons\n"
                "• **[Easter Egg Icons Credit]** -> Iconduck, badges made with photoshop\n"
		"• **[Letter Icons Credit]** -> Made with Python PIL library with Unix based fonts"
            ),
            inline=False
        )
        
        embed.add_field(
            name="🙏 Special Thanks",
            value=(
                "• **github.com/dracos** -> For valid word list\n"
                "• **github.com/cfreshman** -> For wordle answer list\n"
		"• **Wordler Infinity by gherkin21** -> Inspiration for certain features\n"
		"• **You** -> For using the bot :D"
            ),
            inline=False
        )

        embed.add_field(
            name="🤖 AI Usage Disclosure",
            value=(
                "• **Grammar Check** -> Because I make alot errors :(\n"
                "• **Code Refactoring** -> To properly sort my messy code\n"
		"• **Train On Your Data** -> Just Kidding"
            ),
            inline=False
        )

        
        embed.add_field(
            name="📚 Libraries/Tools",
            value=(
                "• **discord.py** -> Discord API Wrapper\n"
		"• **Uptime Robo** -> To keep bot alive :'(\n"
                "• **Supabase** -> Database"
            ),
            inline=False
        )
        
        embed.set_footer(text="Thank You! btw, I take feature requests via /message :)")
        await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot):
    await bot.add_cog(HelpCommands(bot))
