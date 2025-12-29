"""
Race mode commands: /race and /showrace
"""
import discord
from discord.ext import commands, tasks
from discord import app_commands
import random
import datetime
from src.race_game import RaceSession
from src.ui_race import RaceLobbyView, RaceGameView
from src.utils import EMOJIS


class RaceCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.check_race_timeouts.start()
    
    def cog_unload(self):
        self.check_race_timeouts.cancel()
    
    @tasks.loop(seconds=10)
    async def check_race_timeouts(self):
        """Check for expired race sessions."""
        if not hasattr(self.bot, 'race_sessions'): return
        
        # Snapshot keys to avoid runtime error if dict changes
        try:
            active_sessions = [s for s in self.bot.race_sessions.values() if s.status == 'active']
            for session in active_sessions:
                if session.end_time and datetime.datetime.now() > session.end_time:
                    # Time's up!
                    session.status = 'finished'
                    
                    # Notify channel
                    channel = self.bot.get_channel(session.channel_id)
                    if channel:
                        try:
                            await channel.send(
                                f"⏰ **Race Time Limit Reached!**\n"
                                f"The word was **{session.secret.upper()}**.\n"
                                f"Thanks for playing!"
                            )
                        except:
                            pass
                    
                    # Remove from active sessions
                    if session.channel_id in self.bot.race_sessions:
                        del self.bot.race_sessions[session.channel_id]
        except Exception as e:
            print(f"Error in race timeout loop: {e}")

    @check_race_timeouts.before_loop
    async def before_check_race_timeouts(self):
        await self.bot.wait_until_ready()
    
    @app_commands.command(name="race", description="Start a race lobby - compete to solve the same word!")
    async def race(self, interaction: discord.Interaction):
        """Start a race lobby where players compete to solve the same word."""
        # Check if user is banned
        if hasattr(self.bot, 'banned_users') and interaction.user.id in self.bot.banned_users:
            return await interaction.response.send_message(
                "🚫 You are banned from using this bot.",
                ephemeral=True
            )
        
        await interaction.response.defer()
        
        cid = interaction.channel.id
        
        # Check for existing games (no classic, normal, or custom games allowed)
        if cid in self.bot.games:
            return await interaction.followup.send(
                "⚠️ A regular Wordle game is already active in this channel! Use `/stop_game` first.",
                ephemeral=True
            )
        
        if cid in self.bot.custom_games:
            return await interaction.followup.send(
                "⚠️ A custom game is already active in this channel! Use `/stop_game` first.",
                ephemeral=True
            )
        
        # Check if race already exists in this channel
        if cid in self.bot.race_sessions:
            return await interaction.followup.send(
                "⚠️ A race lobby is already active in this channel!",
                ephemeral=True
            )
        
        # Pick a random word for the race
        secret = random.choice(self.bot.secrets)
        
        # Create placeholder message to get message ID
        temp_embed = discord.Embed(
            title="🏁 Creating Race Lobby...",
            description="Setting up the race...",
            color=discord.Color.blue()
        )
        message = await interaction.followup.send(embed=temp_embed)
        
        # Create race session
        race_session = RaceSession(cid, interaction.user, secret, message.id)
        self.bot.race_sessions[cid] = race_session
        
        # Create lobby view
        view = RaceLobbyView(self.bot, race_session)
        embed = view.create_lobby_embed()
        
        # Update message with actual lobby
        await message.edit(embed=embed, view=view)
    
    @app_commands.command(name="showrace", description="Recover your race game if you dismissed it.")
    async def showrace(self, interaction: discord.Interaction):
        """Show the user's active race game if they dismissed it."""
        # Check if user is banned
        if hasattr(self.bot, 'banned_users') and interaction.user.id in self.bot.banned_users:
            return await interaction.response.send_message(
                "🚫 You are banned from using this bot.",
                ephemeral=True
            )
        
        # Find if user has an active race game
        user_race_game = None
        user_race_session = None
        
        for race_session in self.bot.race_sessions.values():
            if race_session.status == 'active' and interaction.user.id in race_session.race_games:
                user_race_game = race_session.race_games[interaction.user.id]
                user_race_session = race_session
                break
        
        if not user_race_game:
            return await interaction.response.send_message(
                "⚠️ No active race game found. Join a race with `/race` first!",
                ephemeral=True
            )
        
        # Recreate the game display
        game = user_race_game
        filled = "●" * game.attempts_used
        empty = "○" * (game.max_attempts - game.attempts_used)
        progress_bar = f"[{filled}{empty}]"
        
        board_display = "\n".join([f"{h['pattern']}" for h in game.history]) if game.history else "No guesses yet."
        
        # Generate keypad
        from src.ui_race import RaceGameView
        view = RaceGameView(self.bot, game, interaction.user, user_race_session)
        keypad = view.get_markdown_keypad(game.used_letters, interaction.user.id)
        
        # Timer check
        end_desc = ""
        if user_race_session.end_time:
             end_ts = int(user_race_session.end_time.timestamp())
             end_desc = f"\nEnds <t:{end_ts}:R>!"

        embed = discord.Embed(
            title=f"🏁 Race Mode | Attempt {game.attempts_used}/{game.max_attempts}",
            color=discord.Color.gold()
        )
        embed.description = f"**Racing against {user_race_session.participant_count} players!**{end_desc}"
        embed.add_field(name="Board", value=board_display, inline=False)
        embed.set_footer(text=f"{game.max_attempts - game.attempts_used} tries left {progress_bar}")
        
        message_content = f"**Keyboard Status:**\n{keypad}"
        
        await interaction.response.send_message(
            content=message_content,
            embed=embed,
            view=view,
            ephemeral=True
        )


async def setup(bot):
    await bot.add_cog(RaceCommands(bot))
