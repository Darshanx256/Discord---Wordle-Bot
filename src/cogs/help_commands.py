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
    @app_commands.choices(feature=[
        app_commands.Choice(name="Wordle (Classic/Simple)", value="wordle"),
        app_commands.Choice(name="Word Rush (Fast-Paced Mode)", value="word_rush"),
        app_commands.Choice(name="Race Mode", value="race"),
        app_commands.Choice(name="Solo Mode", value="solo"),
        app_commands.Choice(name="Custom Games", value="custom"),
        app_commands.Choice(name="Bot Setup", value="bot_setup"),
        app_commands.Choice(name="Progression & Tiers", value="progression"),
        app_commands.Choice(name="Why Legacy Guess Was Removed", value="why_g_removed")
    ])
    async def help_cmd(self, interaction: discord.Interaction, feature: str = None):
        view = HelpView(interaction.user, initial_feature=feature)
        await interaction.response.send_message(embed=view.create_embed(), view=view, ephemeral=True)

    # ============================================================
    # /about command
    # ============================================================
    @app_commands.command(name="about", description="View bot information, credits, and links.")
    async def about_cmd(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="ℹ️ About Wordle Game Bot",
            description=(
                "I would like to express my gratitude to the contributors and resources "
                "that have made Wordle Game Bot possible. This project is dedicated to providing "
                "a high-quality gaming experience to the Discord community."
            ),
            color=discord.Color.gold()
        )

        embed.add_field(
            name="🔗 Important Links",
            value=(
                "• **Website** -> [bit.ly/wordlegamebot](https://bit.ly/wordlegamebot)\n"
                "• **Privacy Policy** -> [Click Here](https://wordle-new.onrender.com/privacy)\n"
                "• **Terms of Service** -> [Click Here](https://wordle-new.onrender.com/tos)\n"
            ),
            inline=False
        )
        
        embed.add_field(
            name="👨‍💻 Development & Maintenance",
            value=(
                "• **Oretsu** -> Lead Developer & Maintainer\n"
                "• Inquiries may be directed via `/message` or to `Ortsx256@proton.me`.\n"
                "• **Ko-Fi** -> [Click Here](https://ko-fi.com/wordlegamebot)\n"
            ),
            inline=False
        )

        embed.add_field(
            name="🎨 Visual Assets",
            value=(
                "• **Icons** -> Octopus Icon by Whitevector (Flaticon)\n"
                "• **Easter Egg Assets** -> Iconduck; custom badges created via Adobe Photoshop\n"
                "• **Typography** -> Letter icons generated with Python PIL using standardized Unix fonts"
            ),
            inline=False
        )
        
        embed.add_field(
            name="🙏 Data Sources & Inspiration",
            value=(
                "• **Word Lists** -> Contributors at `github.com/dracos` and `github.com/cfreshman`\n"
                "• **Inspirations** -> Wordler Infinity by gherkin21, Mudae tea game\n"
                "• **Community** -> My sincere thanks to all players for your continued support"
            ),
            inline=False
        )

        embed.add_field(
            name="📖 Linguistic Data",
            value=(
                "• **British National Corpus (BNC)** -> Comprehensive lemma collection provided by `github.com/skywind3000/lemma.en`"
            ),
            inline=False
        )

        embed.add_field(
            name="🛠️ Infrastructure & Technologies",
            value=(
                "• **discord.py** -> API Integration\n"
                "• **Supabase** -> Database Management\n"
                "• **Assisted Development** -> Utilized for code optimization and grammatical refinement"
            ),
            inline=False
        )
        
        embed.set_footer(text="Wordle Game Bot - v4.6 • Feature requests are welcomed via the /message command.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot):
    await bot.add_cog(HelpCommands(bot))
