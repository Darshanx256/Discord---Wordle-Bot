"""
UI components for Race Mode: lobby views, race game views, and modals.
"""
import discord
from discord import ui
import datetime
from src.config import KEYBOARD_LAYOUT
from src.utils import EMOJIS


class RaceLobbyView(ui.View):
    """View for race lobby with Join/Start/Cancel buttons."""
    
    def __init__(self, bot, race_session):
        super().__init__(timeout=120)  # 2 minutes
        self.bot = bot
        self.race_session = race_session
        self.update_buttons()
    
    def update_buttons(self):
        """Update button states based on race session state."""
        # Disable start if less than 2 participants
        for child in self.children:
            if hasattr(child, 'custom_id'):
                if child.custom_id == 'race_start':
                    child.disabled = not self.race_session.can_start
    
    @ui.button(label="Join Race", style=discord.ButtonStyle.success, emoji="🏁", custom_id="race_join")
    async def join_button(self, interaction: discord.Interaction, button: ui.Button):
        """Handle participant joining the race."""
        if self.race_session.add_participant(interaction.user):
            await interaction.response.send_message(
                f"✅ You've joined the race! **{self.race_session.participant_count}** participants ready.",
                ephemeral=True
            )
            # Update lobby embed
            self.update_buttons()
            embed = self.create_lobby_embed()
            try:
                await interaction.message.edit(embed=embed, view=self)
            except:
                pass
        else:
            await interaction.response.send_message("⚠️ You've already joined this race!", ephemeral=True)
    
    @ui.button(label="Start Race", style=discord.ButtonStyle.primary, emoji="🚀", custom_id="race_start")
    async def start_button(self, interaction: discord.Interaction, button: ui.Button):
        """Start the race if enough participants and user is starter."""
        if interaction.user.id != self.race_session.started_by.id:
            return await interaction.response.send_message(
                "❌ Only the race starter can begin the race!",
                ephemeral=True
            )
        
        if not self.race_session.can_start:
            return await interaction.response.send_message(
                "⚠️ Need at least 2 participants to start!",
                ephemeral=True
            )
        
        await interaction.response.defer()
        
        # Disable all buttons
        for child in self.children:
            child.disabled = True
        
        # Update lobby message
        embed = discord.Embed(
            title="🏁 Race Starting!",
            description=f"**{self.race_session.participant_count}** racers are getting ready...",
            color=discord.Color.gold()
        )
        await interaction.message.edit(embed=embed, view=self)
        
        # Start race games for each participant
        from src.game import WordleGame
        from src.ui_race import RaceGameView
        
        for user_id, user in self.race_session.participants.items():
            game = WordleGame(self.race_session.secret, 0, user, 0)
            self.race_session.race_games[user_id] = game
            
            # Send ephemeral race game to each participant
            board_display = "No guesses yet."
            keypad = self.get_markdown_keypad(game.used_letters, user_id)
            progress_bar = "[○○○○○○]"
            
            # Set end time
            self.race_session.end_time = datetime.datetime.now() + datetime.timedelta(minutes=self.race_session.duration_minutes)
            end_ts = int(self.race_session.end_time.timestamp())

            embed = discord.Embed(title=f"🏁 Race Mode | Attempt 0/{game.max_attempts}", color=discord.Color.gold())
            embed.description = f"**Race against {self.race_session.participant_count} players!**\nEnds <t:{end_ts}:R>!"
            embed.add_field(name="Board", value=board_display, inline=False)
            embed.set_footer(text=f"{game.max_attempts} tries left {progress_bar}")
            
            message_content = f"**Keyboard Status:**\n{keypad}"
            
            view = RaceGameView(self.bot, game, user, self.race_session)
            try:
                await user.send(content=message_content, embed=embed, view=view)
            except:
                # Fallback: send in channel if DM fails
                channel = self.bot.get_channel(self.race_session.channel_id)
                if channel:
                    await channel.send(
                        f"{user.mention} - Your race game!",
                        embed=embed,
                        view=view
                    )
        
        self.race_session.status = 'active'
        self.stop()
    
    @ui.button(label="Cancel", style=discord.ButtonStyle.danger, custom_id="race_cancel")
    async def cancel_button(self, interaction: discord.Interaction, button: ui.Button):
        """Cancel the race lobby."""
        if interaction.user.id != self.race_session.started_by.id:
            return await interaction.response.send_message(
                "❌ Only the race starter can cancel!",
                ephemeral=True
            )
        
        await interaction.response.defer()
        
        # Remove race session
        if self.race_session.channel_id in self.bot.race_sessions:
            del self.bot.race_sessions[self.race_session.channel_id]
        
        embed = discord.Embed(
            title="❌ Race Cancelled",
            description="The race has been cancelled by the starter.",
            color=discord.Color.red()
        )
        await interaction.message.edit(embed=embed, view=None)
        self.stop()
    
    def create_lobby_embed(self):
        """Create the lobby embed showing participants."""
        embed = discord.Embed(
            title="🏁 Race Lobby",
            description=f"Waiting for racers... **{self.race_session.participant_count}** joined\nRace Duration: **{self.race_session.duration_minutes} mins**",
            color=discord.Color.blue()
        )
        
        participants_list = "\n".join([f"• {user.display_name}" for user in self.race_session.participants.values()])
        embed.add_field(name="Participants", value=participants_list or "No one yet!", inline=False)
        embed.add_field(
            name="How to Play",
            value="• Click **Join Race** to participate\n"
                  "• At least **2 players** needed\n"
                  "• Starter clicks **Start** when ready\n"
                  "• Everyone races to solve the same word!",
            inline=False
        )
        
        time_remaining = 120 - (datetime.datetime.now() - self.race_session.start_time).total_seconds()
        if time_remaining > 0:
            embed.set_footer(text=f"⏰ Lobby expires in {int(time_remaining)}s • Min 2 players to start")
        else:
            embed.set_footer(text="⏰ Lobby expired!")
        
        return embed
    
    async def on_timeout(self):
        """Handle lobby timeout after 2 minutes."""
        if self.race_session.status == 'waiting':
            # Remove race session
            if self.race_session.channel_id in self.bot.race_sessions:
                del self.bot.race_sessions[self.race_session.channel_id]
            
            # Try to update message
            try:
                channel = self.bot.get_channel(self.race_session.channel_id)
                if channel:
                    message = await channel.fetch_message(self.race_session.lobby_message_id)
                    embed = discord.Embed(
                        title="⏰ Race Lobby Expired",
                        description="The race lobby timed out after 2 minutes.",
                        color=discord.Color.dark_grey()
                    )
                    await message.edit(embed=embed, view=None)
            except:
                pass
    
    def get_markdown_keypad(self, used_letters: dict, user_id: int):
        """Generate markdown keyboard status."""
        lines = []
        for row in KEYBOARD_LAYOUT:
            chars = []
            for ch in row:
                ch_lower = ch.lower()
                if ch_lower in used_letters['correct']:
                    emoji_key = f"key_{ch_lower}_green"
                    chars.append(EMOJIS.get(emoji_key, f"🟩{ch}"))
                elif ch_lower in used_letters['present']:
                    emoji_key = f"key_{ch_lower}_yellow"
                    chars.append(EMOJIS.get(emoji_key, f"🟨{ch}"))
                elif ch_lower in used_letters['absent']:
                    emoji_key = f"key_{ch_lower}_grey"
                    chars.append(EMOJIS.get(emoji_key, f"⬜{ch}"))
                else:
                    emoji_key = f"key_{ch_lower}"
                    chars.append(EMOJIS.get(emoji_key, ch))
            lines.append("".join(chars))
        return "\n".join(lines)


class RaceGuessModal(ui.Modal, title="🏁 Race Guess"):
    """Modal for making guesses in race mode."""
    
    guess_input = ui.TextInput(label="5-Letter Word", min_length=5, max_length=5, required=True)
    
    def __init__(self, bot, game, view_ref, race_session):
        super().__init__()
        self.bot = bot
        self.game = game
        self.view_ref = view_ref
        self.race_session = race_session
    
    async def on_submit(self, interaction: discord.Interaction):
        """Handle guess submission in race mode."""
        guess = self.guess_input.value.strip().lower()
        
        # Validate word
        if guess not in self.bot.valid_set:
            return await interaction.response.send_message(
                f"❌ **{guess.upper()}** is not in the dictionary!",
                ephemeral=True
            )
        
        # Check for duplicate
        if self.game.is_duplicate(guess):
            return await interaction.response.send_message(
                f"⚠️ You've already tried **{guess.upper()}**!",
                ephemeral=True
            )
        
        # Process the guess
        pattern, won, game_over = self.game.process_turn(guess, interaction.user)
        
        # Update the game display
        await interaction.response.defer()
        
        filled = "●" * self.game.attempts_used
        empty = "○" * (self.game.max_attempts - self.game.attempts_used)
        progress_bar = f"[{filled}{empty}]"
        
        board_display = "\n".join([f"{h['pattern']}" for h in self.game.history])
        keypad = self.view_ref.get_markdown_keypad(self.game.used_letters, interaction.user.id)
        
        if won:
            # Record completion
            time_taken = (datetime.datetime.now() - self.game.start_time).total_seconds()
            self.race_session.record_completion(interaction.user.id, True, time_taken)
            rank = self.race_session.get_rank(interaction.user.id)
            
            # Calculate rewards
            from src.mechanics.rewards import calculate_race_rewards
            rewards = calculate_race_rewards(self.bot, interaction.user.id, self.game, rank)
            
            embed = discord.Embed(title=f"🏆 Victory! - Rank #{rank}", color=discord.Color.gold())
            embed.description = f"You solved it in **{self.game.attempts_used}/6** tries!"
            embed.add_field(name="Board", value=board_display, inline=False)
            embed.add_field(name="⏱️ Time", value=f"{time_taken:.1f}s", inline=True)
            embed.add_field(name="🎁 Rewards", value=rewards['message'], inline=False)
            
            self.view_ref.disable_all()
            await interaction.edit_original_response(content=f"**Keyboard:**\n{keypad}", embed=embed, view=self.view_ref)
            
            # Announce completion in channel
            channel = self.bot.get_channel(self.race_session.channel_id)
            if channel:
                await channel.send(f"🏁 Congratulations {interaction.user.mention}, you finished **#{rank}**!")
            
            # Check if all completed
            if self.race_session.all_completed:
                await self.handle_race_end(channel)
        
        elif game_over:
            # Record completion as failed
            time_taken = (datetime.datetime.now() - self.game.start_time).total_seconds()
            self.race_session.record_completion(interaction.user.id, False, time_taken)
            
            embed = discord.Embed(title="💔 Out of Attempts", color=discord.Color.red())
            embed.description = f"You ran out of tries!"
            embed.add_field(name="Board", value=board_display, inline=False)
            
            # Don't reveal word yet - wait for all to finish
            if not self.race_session.all_completed:
                embed.set_footer(text="Waiting for others to finish...")
            
            self.view_ref.disable_all()
            await interaction.edit_original_response(content=f"**Keyboard:**\n{keypad}", embed=embed, view=self.view_ref)
            
            # Check if all completed
            if self.race_session.all_completed:
                channel = self.bot.get_channel(self.race_session.channel_id)
                if channel:
                    await self.handle_race_end(channel)
        
        else:
            # Continue playing
            end_ts = int(self.race_session.end_time.timestamp()) if self.race_session.end_time else 0
            embed = discord.Embed(
                title=f"🏁 Race Mode | Attempt {self.game.attempts_used}/{self.game.max_attempts}",
                color=discord.Color.blue()
            )
            embed.description = f"Ends <t:{end_ts}:R>"
            embed.add_field(name="Board", value=board_display, inline=False)
            embed.set_footer(text=f"{self.game.max_attempts - self.game.attempts_used} tries left {progress_bar}")
            
            await interaction.edit_original_response(content=f"**Keyboard:**\n{keypad}", embed=embed, view=self.view_ref)
    
    async def handle_race_end(self, channel):
        """Handle race end - reveal word if anyone failed."""
        if self.race_session.anyone_failed:
            await channel.send(
                f"**🏁 Race Over!** The word was **{self.game.secret.upper()}**."
            )
        else:
            await channel.send(
                f"**🏁 Race Over!** Everyone solved it! Great job! 🎉"
            )
        
        # Clean up race session
        if self.race_session.channel_id in self.bot.race_sessions:
            del self.bot.race_sessions[self.race_session.channel_id]


class RaceGameView(ui.View):
    """View for individual race game (buttons)."""
    
    def __init__(self, bot, game, user, race_session):
        super().__init__(timeout=900)  # 15 minutes
        self.bot = bot
        self.game = game
        self.user = user
        self.race_session = race_session
    
    def disable_all(self):
        for child in self.children:
            child.disabled = True
    
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("❌ This is not your race!", ephemeral=True)
            return False
        return True
    
    @ui.button(label="Make Guess", style=discord.ButtonStyle.primary, emoji="📝")
    async def guess_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(RaceGuessModal(self.bot, self.game, self, self.race_session))
    
    @ui.button(label="End Race", style=discord.ButtonStyle.danger)
    async def end_race_button(self, interaction: discord.Interaction, button: ui.Button):
        """End the race early (counts as forfeit)."""
        # Record as failed
        time_taken = (datetime.datetime.now() - self.game.start_time).total_seconds()
        self.race_session.record_completion(interaction.user.id, False, time_taken)
        
        embed = discord.Embed(
            title="🏳️ Race Forfeited",
            description="You ended the race early.",
            color=discord.Color.dark_grey()
        )
        
        self.disable_all()
        await interaction.response.edit_message(embed=embed, view=self)
    
    def get_markdown_keypad(self, used_letters: dict, user_id: int):
        """Generate markdown keyboard status."""
        lines = []
        for row in KEYBOARD_LAYOUT:
            chars = []
            for ch in row:
                ch_lower = ch.lower()
                if ch_lower in used_letters['correct']:
                    emoji_key = f"key_{ch_lower}_green"
                    chars.append(EMOJIS.get(emoji_key, f"🟩{ch}"))
                elif ch_lower in used_letters['present']:
                    emoji_key = f"key_{ch_lower}_yellow"
                    chars.append(EMOJIS.get(emoji_key, f"🟨{ch}"))
                elif ch_lower in used_letters['absent']:
                    emoji_key = f"key_{ch_lower}_grey"
                    chars.append(EMOJIS.get(emoji_key, f"⬜{ch}"))
                else:
                    emoji_key = f"key_{ch_lower}"
                    chars.append(EMOJIS.get(emoji_key, ch))
            lines.append("".join(chars))
        return "\n".join(lines)
