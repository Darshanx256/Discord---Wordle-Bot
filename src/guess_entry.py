import discord
from src.discord_integrations.server import build_integration_link


class GuessInputModal(discord.ui.Modal, title="Submit Guess"):
    guess = discord.ui.TextInput(
        label="Enter your guess",
        placeholder="Type a word (Wordle: 5 letters)",
        min_length=1,
        max_length=32,
    )

    def __init__(self, bot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        cog = self.bot.get_cog("GuessHandler")
        if not cog:
            return await interaction.response.send_message("Guess handler is unavailable.", ephemeral=True)

        word = self.guess.value.strip()
        await cog.handle_interaction_guess(interaction, word)


class GuessEntryView(discord.ui.View):
    def __init__(self, bot, *, timeout: float = 3600):
        super().__init__(timeout=timeout)
        self.bot = bot

    @discord.ui.button(label="Guess (Modal)", style=discord.ButtonStyle.secondary, emoji="📝")
    async def guess_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(GuessInputModal(self.bot))

    @discord.ui.button(label="Open Integration UI", style=discord.ButtonStyle.primary, emoji="🌐")
    async def open_integration_ui(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Prefer Discord-native Activity launch when available.
        launch_activity = getattr(interaction.response, "launch_activity", None)
        if callable(launch_activity):
            try:
                await launch_activity()
                return
            except discord.HTTPException:
                # Fall through to URL fallback if Activity launch fails.
                pass

        link = build_integration_link(self.bot, interaction.user.id, interaction.channel_id)
        if not link:
            return await interaction.response.send_message(
                "⚠️ Activity launch is unavailable and no active game link was found.",
                ephemeral=True,
            )

        launch_view = discord.ui.View(timeout=180)
        launch_view.add_item(discord.ui.Button(label="Open Live Board", style=discord.ButtonStyle.link, url=link))
        await interaction.response.send_message(
            "Activity launch is unavailable here, using web link fallback.",
            ephemeral=True,
            view=launch_view,
        )
