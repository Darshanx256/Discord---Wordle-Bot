import discord
from discord.ext import commands
from discord import app_commands

from src.database import fetch_guild_allowed_channels
from src.setup_wizard import SetupWizardView


class SetupCommand(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="setup", description="Open interactive setup wizard.")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def setup_wizard(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        if guild_id is None or interaction.guild is None:
            return await interaction.response.send_message("Server-only command.", ephemeral=True)

        channels = await fetch_guild_allowed_channels(self.bot, guild_id)
        self.bot._set_channel_access_cache(guild_id, channels)
        view = SetupWizardView(self.bot, interaction.guild, interaction.user.id, channels)
        await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=True)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.errors.MissingPermissions):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ You need `Manage Server` permission to use `/setup`.",
                    ephemeral=True
                )
            return
        if not interaction.response.is_done():
            await interaction.response.send_message("❌ Setup command failed.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(SetupCommand(bot))
