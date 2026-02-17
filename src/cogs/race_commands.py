"""
Race mode commands: /race and /showrace
"""
import discord
from discord.ext import commands, tasks
from discord import app_commands
import datetime
import time
from src.utils import format_attempt_footer
import asyncio
from src.race_game import RaceSession
from src.ui_race import RaceLobbyView, RaceGameView
from src.utils import EMOJIS


class RaceCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._race_timers = {} # channel_id: Task
    
    def cog_unload(self):
        for task in self._race_timers.values():
            task.cancel()
    
    async def _run_race_timer(self, channel_id, session):
        """
        Production-Ready Timer Design:
        - Use a single async task per race
        - Store a fixed end time using time.monotonic()
        - Sleep dynamically: long sleeps when far, short sleeps near finish
        """
        # print(f"🚀 Starting timer for race in {channel_id}")
        
        try:
            while session.status == 'active':
                now = time.monotonic()
                remaining = session.monotonic_end_time - now
                
                if remaining <= 0:
                    break
                
                # Dynamic sleep logic
                if remaining > 60:
                    sleep_time = 30
                elif remaining > 10:
                    sleep_time = 5
                elif remaining > 2:
                    sleep_time = 1
                elif remaining > 0.05:
                    sleep_time = 0.05 # High frequency loop as we approach the exact second
                else:
                    sleep_time = 0 # Immediate break soon
                
                if sleep_time > 0:
                    await asyncio.sleep(min(sleep_time, remaining))
                else:
                    break
                
            # Time's up or session marked finished
            if session.status == 'active':
                session.status = 'finished'
                # print(f"⏰ Race in {channel_id} timed out. Concluding...")
                from src.ui_race import send_race_summary
                await send_race_summary(self.bot, channel_id, session)
                
        except asyncio.CancelledError:
            # print(f"🛑 Timer for race {channel_id} cancelled.")
            pass
        except Exception as e:
            print(f"❌ Error in race timer {channel_id}: {e}")
        finally:
            self._race_timers.pop(channel_id, None)

    async def start_race_timer(self, channel_id, session):
        """Helper to launch the timer task."""
        if channel_id in self._race_timers:
            self._race_timers[channel_id].cancel()
        
        task = asyncio.create_task(self._run_race_timer(channel_id, session))
        self._race_timers[channel_id] = task
    
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
        
        # Create placeholder message to get message ID
        temp_embed = discord.Embed(
            title="🏁 Creating Race Lobby...",
            description="Setting up the race...",
            color=discord.Color.blue()
        )
        message = await interaction.followup.send(embed=temp_embed)
        
        # Create race session (Secret will be picked when it starts)
        race_session = RaceSession(cid, interaction.user, lobby_message_id=message.id)
        self.bot.race_sessions[cid] = race_session
        
        # Create lobby view
        view = RaceLobbyView(self.bot, race_session)
        embed = view.create_lobby_embed()
        
        # Update message with actual lobby
        await message.edit(embed=embed, view=view)
    
    @app_commands.command(name="show_race", description="Recover your race game if you dismissed it.")
    async def show_race(self, interaction: discord.Interaction):
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
        board_display = "\n".join([f"{h['pattern']}" for h in game.history]) if game.history else "No guesses yet."
        
        # Generate keypad
        from src.ui_race import RaceGameView
        view = RaceGameView(self.bot, game, interaction.user, user_race_session)
        keypad = view.get_markdown_keypad(game.used_letters, interaction.user.id)
        
        # Timer check
        end_desc = ""
        end_ts = None
        is_ended = (user_race_session.status == 'finished')
        if user_race_session.end_time:
             end_ts = int(user_race_session.end_time.timestamp())
             is_ended = int(time.time()) >= end_ts or user_race_session.status == 'finished'
             
             if is_ended:
                 end_desc = f"\n**Ended** <t:{end_ts}:R>!"
             else:
                 end_desc = f"\nEnds <t:{end_ts}:R>!"

        embed = discord.Embed(color=discord.Color.gold())
        timer_label = "Ended" if is_ended else "Ends"
        
        embed.description = (
            f"{timer_label} <t:{end_ts}:R>\n\n"
            f"{board_display}\n\n"
            f"{keypad}"
        )
        used = max(0, min(game.attempts_used, game.max_attempts))
        filled = "•" * used
        empty = "○" * (game.max_attempts - used)
        bar = f"[{filled}{empty}]"
        time_text = f"<t:{end_ts}:R>" if end_ts else "N/A"
        footer = f"{bar} • Players: {user_race_session.participant_count} • Time: {time_text}"
        embed.set_footer(text=footer)
        if game.history:
            last_guess = (game.history[-1].get('word') or '').upper()
            if last_guess:
                embed.set_author(
                    name=f"{interaction.user.mention} guessed {last_guess}",
                    icon_url=interaction.user.display_avatar.url
                )

        await interaction.response.send_message(
            embed=embed,
            view=view,
            ephemeral=True
        )


async def setup(bot):
    await bot.add_cog(RaceCommands(bot))
