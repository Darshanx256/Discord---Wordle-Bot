
import discord
from discord.ext import commands
from discord import app_commands
import time
import datetime

class GeneralCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="ping", description="Check the bot's latency.")
    async def ping(self, interaction: discord.Interaction):
        t0 = time.perf_counter()
        await interaction.response.send_message("Running latency probe...", ephemeral=True)
        t1 = time.perf_counter()

        t2 = time.perf_counter()
        ws_latency_ms = self.bot.latency * 1000
        ack_rtt_ms = (t1 - t0) * 1000
        now_utc = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")
        node = interaction.client.user.id if interaction.client and interaction.client.user else "unknown"

        report = (
            "pong!\n"
            f"utc          {now_utc}\n"
            f"node_id      {node}\n"
            f"gateway_ms   {ws_latency_ms:.2f}\n"
            f"ack_ms       {ack_rtt_ms:.2f}\n"
        )

        await interaction.edit_original_response(content=f"```text\n{report}```")
        t3 = time.perf_counter()
        edit_rtt_ms = (t3 - t2) * 1000

        report = report + f"edit_ms      {edit_rtt_ms:.2f}\n"
        await interaction.edit_original_response(content=f"```text\n{report}```")

async def setup(bot):
    await bot.add_cog(GeneralCommands(bot))
