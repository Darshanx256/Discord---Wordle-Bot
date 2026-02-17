import discord
from discord.ext import commands
from discord import app_commands

from src.database import (
    clear_guild_allowed_channels,
    fetch_guild_allowed_channels,
    upsert_guild_allowed_channels,
)


class ChannelSetup(commands.GroupCog, name="channel_setup"):
    """Optional channel restriction setup for gameplay commands."""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="add", description="Allow gameplay commands in a channel.")
    @app_commands.describe(channel="Channel to allow gameplay commands in")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def add(self, interaction: discord.Interaction, channel: discord.TextChannel):
        guild_id = interaction.guild_id
        if guild_id is None:
            return await interaction.response.send_message("Server-only command.", ephemeral=True)

        current = await fetch_guild_allowed_channels(self.bot, guild_id)
        channels = set(current or set())
        channels.add(channel.id)

        ok = await upsert_guild_allowed_channels(self.bot, guild_id, channels)
        if not ok:
            return await interaction.response.send_message(
                "❌ Failed to update channel setup. Please try again.",
                ephemeral=True
            )

        self.bot._set_channel_access_cache(guild_id, channels)
        await interaction.response.send_message(
            f"✅ Added {channel.mention}. Gameplay commands are now allowed there.",
            ephemeral=True
        )

    @app_commands.command(name="remove", description="Remove a channel from gameplay allowlist.")
    @app_commands.describe(channel="Channel to remove from gameplay allowlist")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def remove(self, interaction: discord.Interaction, channel: discord.TextChannel):
        guild_id = interaction.guild_id
        if guild_id is None:
            return await interaction.response.send_message("Server-only command.", ephemeral=True)

        current = await fetch_guild_allowed_channels(self.bot, guild_id)
        channels = set(current or set())

        if channel.id not in channels:
            return await interaction.response.send_message(
                f"ℹ️ {channel.mention} is not in the allowlist.",
                ephemeral=True
            )

        channels.remove(channel.id)
        ok = await upsert_guild_allowed_channels(self.bot, guild_id, channels)
        if not ok:
            return await interaction.response.send_message(
                "❌ Failed to update channel setup. Please try again.",
                ephemeral=True
            )

        self.bot._set_channel_access_cache(guild_id, channels if channels else None)
        if channels:
            return await interaction.response.send_message(
                f"✅ Removed {channel.mention} from gameplay allowlist.",
                ephemeral=True
            )

        await interaction.response.send_message(
            "✅ Removed the final channel. Setup is now cleared, so gameplay commands work everywhere.",
            ephemeral=True
        )

    @app_commands.command(name="list", description="List gameplay channel setup status.")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def list(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        if guild_id is None:
            return await interaction.response.send_message("Server-only command.", ephemeral=True)

        entry = await self.bot._refresh_channel_access_cache(guild_id)
        configured = entry.get("configured", False)
        channels = entry.get("channels", set())

        if not configured:
            return await interaction.response.send_message(
                "ℹ️ No channel setup configured. Gameplay commands currently work in all channels.",
                ephemeral=True
            )

        visible_mentions = []
        for channel_id in sorted(channels):
            ch = interaction.guild.get_channel(channel_id)
            visible_mentions.append(ch.mention if ch else f"<#{channel_id}>")

        description = "\n".join(f"• {m}" for m in visible_mentions) if visible_mentions else "• (none)"
        embed = discord.Embed(
            title="Gameplay Channel Setup",
            description=description,
            color=discord.Color.blue()
        )
        embed.set_footer(text="Use /channel_setup clear to disable restrictions.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="clear", description="Clear setup and allow gameplay commands in all channels.")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def clear(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        if guild_id is None:
            return await interaction.response.send_message("Server-only command.", ephemeral=True)

        ok = await clear_guild_allowed_channels(self.bot, guild_id)
        if not ok:
            return await interaction.response.send_message(
                "❌ Failed to clear channel setup. Please try again.",
                ephemeral=True
            )

        self.bot._set_channel_access_cache(guild_id, None)
        await interaction.response.send_message(
            "✅ Channel setup cleared. Gameplay commands now work in all channels.",
            ephemeral=True
        )

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.errors.MissingPermissions):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ You need `Manage Server` permission to use this command.",
                    ephemeral=True
                )
            return

        if not interaction.response.is_done():
            await interaction.response.send_message(
                "❌ Channel setup command failed.",
                ephemeral=True
            )


async def setup(bot):
    await bot.add_cog(ChannelSetup(bot))
