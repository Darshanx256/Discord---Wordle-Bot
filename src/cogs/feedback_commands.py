"""
Feedback commands: /message for user feedback submission
"""
import discord
from discord.ext import commands
from discord import app_commands, ui
import datetime
import os


class FeedbackModal(ui.Modal, title="📨 Send Feedback"):
    """Modal for collecting user feedback."""
    
    title_input = ui.TextInput(
        label="Title",
        placeholder="Brief description of your feedback",
        max_length=100,
        min_length=3,
        required=True
    )
    
    content_input = ui.TextInput(
        label="Content",
        placeholder="Feature requests, bug reports, questions...",
        max_length=1000,
        min_length=10,
        required=True,
        style=discord.TextStyle.paragraph
    )
    
    def __init__(self):
        super().__init__()
    
    async def on_submit(self, interaction: discord.Interaction):
        """Handle feedback submission - stores to Supabase."""
        title = self.title_input.value.strip()
        content = self.content_input.value.strip()
        
        # Store in Supabase
        try:
            feedback_data = {
                'timestamp': datetime.datetime.utcnow().isoformat(),
                'title': title,
                'content': content,
                'user_id': interaction.user.id # This is the 'id' column requested
            }
            
            interaction.client.supabase_client.table('feedback').insert(feedback_data).execute()
            
            await interaction.response.send_message(
                "✅ **Feedback submitted successfully!**\n"
                "Thank you for your input. Your feedback helps improve the bot!",
                ephemeral=True
            )
        except Exception as e:
            print(f"Failed to save feedback to Supabase: {e}")
            await interaction.response.send_message(
                "❌ Failed to save feedback. Please try again later or contact the bot developer directly.",
                ephemeral=True
            )


class FeedbackCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @app_commands.command(name="message", description="Send feedback, bug reports, or feature requests to the bot developer.")
    async def message(self, interaction: discord.Interaction):
        """Present feedback modal to user."""
        # Check if user is banned
        if hasattr(self.bot, 'banned_users') and interaction.user.id in self.bot.banned_users:
            return await interaction.response.send_message(
                "🚫 You are banned from using this bot.",
                ephemeral=True
            )
        
        modal = FeedbackModal()
        await interaction.response.send_modal(modal)


async def setup(bot):
    await bot.add_cog(FeedbackCommands(bot))
