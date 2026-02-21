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
        async def _send_ephemeral(*, content: str, view=None):
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(content, ephemeral=True, view=view)
                    return
            except (discord.NotFound, discord.HTTPException):
                pass

            try:
                await interaction.followup.send(content, ephemeral=True, view=view)
                return
            except (discord.NotFound, discord.HTTPException):
                pass

            # Last-resort fallback if interaction token already expired.
            if interaction.channel:
                try:
                    await interaction.channel.send(f"{interaction.user.mention} {content}", view=view)
                except (discord.NotFound, discord.HTTPException, discord.Forbidden):
                    pass

        # Prefer Discord-native Activity launch when available.
        # Support both interaction.response.launch_activity and interaction.launch_activity variants.
        launch_candidates = [
            getattr(interaction.response, "launch_activity", None),
            getattr(interaction, "launch_activity", None),
        ]
        for launch_activity in launch_candidates:
            if not callable(launch_activity):
                continue
            try:
                await launch_activity()
                return
            except Exception as e:
                err_code = getattr(e, "code", None)
                err_status = getattr(e, "status", None)
                err_text = str(e)
                print(f"⚠️ launch_activity failed: code={err_code} status={err_status} err={err_text}")
                # Fall through to URL fallback if Activity launch is unsupported/fails here.
                break

        link = build_integration_link(self.bot, interaction.user.id, interaction.channel_id)
        if not link:
            return await _send_ephemeral(
                content="⚠️ Activity launch is unavailable and no active game link was found."
            )

        launch_view = discord.ui.View(timeout=180)
        launch_view.add_item(discord.ui.Button(label="Open Live Board", style=discord.ButtonStyle.link, url=link))
        await _send_ephemeral(
            content="Activity launch is unavailable here, using web link fallback. Check bot logs for launch_activity error details.",
            view=launch_view,
        )
