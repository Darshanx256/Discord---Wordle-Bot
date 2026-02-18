import discord


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
