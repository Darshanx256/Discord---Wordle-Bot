import discord

from src.database import (
    clear_guild_allowed_channels,
    fetch_guild_allowed_channels,
    upsert_guild_allowed_channels,
)


def _format_channel_lines(guild: discord.Guild, channel_ids: set[int]) -> str:
    if not channel_ids:
        return "No channels configured."
    lines = []
    for channel_id in sorted(channel_ids):
        ch = guild.get_channel(channel_id)
        lines.append(f"• {ch.mention if ch else f'<#{channel_id}>'}")
    return "\n".join(lines)


class SetupChannelSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=25,
            placeholder="Select one or more gameplay channels..."
        )

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if not isinstance(view, SetupWizardView):
            return
        view.pending_channel_ids = {int(ch.id) for ch in self.values}
        await interaction.response.edit_message(embed=view.build_embed())


class SetupWizardView(discord.ui.View):
    def __init__(self, bot, guild: discord.Guild, invoker_id: int, current_channels: set[int] | None):
        super().__init__(timeout=300)
        self.bot = bot
        self.guild = guild
        self.invoker_id = invoker_id
        self.current_channel_ids = set(current_channels or set())
        self.pending_channel_ids = set(self.current_channel_ids)
        self.add_item(SetupChannelSelect())

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="Bot Setup Wizard",
            color=discord.Color.blurple(),
            description=(
                "This setup is optional. If you clear setup, gameplay commands work in all channels.\n\n"
                "**Current Allowed Channels:**\n"
                f"{_format_channel_lines(self.guild, self.current_channel_ids)}\n\n"
                "**Pending Selection:**\n"
                f"{_format_channel_lines(self.guild, self.pending_channel_ids)}"
            )
        )
        embed.set_footer(text="Select channels, then click Save.")
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message("❌ This setup menu is not for you.", ephemeral=True)
            return False
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "❌ You need `Manage Server` permission to use setup.",
                ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Save Selection", style=discord.ButtonStyle.success)
    async def save_selection(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.pending_channel_ids:
            await interaction.response.send_message(
                "⚠️ Select at least one channel or click Clear Setup.",
                ephemeral=True
            )
            return

        ok = await upsert_guild_allowed_channels(self.bot, self.guild.id, self.pending_channel_ids)
        if not ok:
            await interaction.response.send_message("❌ Failed to save setup.", ephemeral=True)
            return

        self.current_channel_ids = set(self.pending_channel_ids)
        self.bot._set_channel_access_cache(self.guild.id, self.current_channel_ids)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Clear Setup", style=discord.ButtonStyle.danger)
    async def clear_setup(self, interaction: discord.Interaction, button: discord.ui.Button):
        ok = await clear_guild_allowed_channels(self.bot, self.guild.id)
        if not ok:
            await interaction.response.send_message("❌ Failed to clear setup.", ephemeral=True)
            return

        self.current_channel_ids = set()
        self.pending_channel_ids = set()
        self.bot._set_channel_access_cache(self.guild.id, None)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        channels = await fetch_guild_allowed_channels(self.bot, self.guild.id)
        self.current_channel_ids = set(channels or set())
        self.pending_channel_ids = set(self.current_channel_ids)
        self.bot._set_channel_access_cache(self.guild.id, channels)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


class SetupLauncherView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=86400)
        self.bot = bot

    @discord.ui.button(label="Open Setup Wizard", style=discord.ButtonStyle.primary)
    async def open_setup_wizard(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            await interaction.response.send_message("Server-only setup.", ephemeral=True)
            return
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "❌ You need `Manage Server` permission to run setup.",
                ephemeral=True
            )
            return

        channels = await fetch_guild_allowed_channels(self.bot, interaction.guild.id)
        self.bot._set_channel_access_cache(interaction.guild.id, channels)
        view = SetupWizardView(self.bot, interaction.guild, interaction.user.id, channels)
        await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=True)
