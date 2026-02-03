
import discord
from discord.ext import commands
from discord import app_commands
import time

class GeneralCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="ping", description="Check the bot's latency.")
    async def ping(self, interaction: discord.Interaction):
        # 1. Capture start time
        start_time = time.monotonic()
        
        # 2. Key Step: Send initial response (this actually triggers the API call we want to measure)
        # We use defer? No, user wants "says pong".
        # If we respond, that IS the API call.
        await interaction.response.send_message("Pong... 🏓", ephemeral=True)
        
        # 3. Capture end time
        end_time = time.monotonic()
        
        # 4. Calculate API Latency
        api_latency_ms = (end_time - start_time) * 1000
        
        # 5. Get Websocket Latency (from the bot's heartbeat)
        ws_latency_ms = round(self.bot.latency * 1000)
        
        # 6. Edit the original message to show stats
        # Note: interaction.edit_original_response is the way to edit the initial response.
        await interaction.edit_original_response(content=f"Pong! 🏓\n📡 **API Latency:** `{int(api_latency_ms)}ms`\n💓 **WS Latency:** `{ws_latency_ms}ms`")

async def setup(bot):
    await bot.add_cog(GeneralCommands(bot))
