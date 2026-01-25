import discord
from discord import ui
import datetime
import time
import asyncio
from src.config import KEYBOARD_LAYOUT
from src.utils import EMOJIS


class RaceLobbyView(ui.View):
    """View for race lobby with Join/Start/Cancel buttons."""
    
    def __init__(self, bot, race_session):
        super().__init__(timeout=300)  # 5 minutes lobby timeout
        self.bot = bot
        self.race_session = race_session
        self.update_buttons()
    
    def update_buttons(self):
        """Update button states based on race session state."""
        # Disable start if less than 2 participants
        # If race is active, show ONLY Open Board
        
        is_active = self.race_session.status == 'active'
        
        # Clear existing buttons if we are restructuring for active state
        if is_active:
            self.clear_items()
            
            # Add Open Board button
            open_btn = ui.Button(label="Open Race Board", style=discord.ButtonStyle.success, emoji="🎮", custom_id="race_open_board")
            open_btn.callback = self.open_board_button
            self.add_item(open_btn)
            return

        # Lobby State Buttons
        for child in self.children:
            if hasattr(child, 'custom_id'):
                if child.custom_id == 'race_start':
                    # Enable start only if 2+ participants
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
        
        # Defer immediately as we have setup work
        await interaction.response.defer()
        
        # Set end time (Standardized to whole seconds for perfect Discord/Server sync)
        end_ts = int(time.time() + (self.race_session.duration_minutes * 60))
        self.race_session.end_time = datetime.datetime.fromtimestamp(end_ts)
        self.race_session.monotonic_end_time = time.monotonic() + (end_ts - time.time())
        
        # Pick the secret from the synchronized bitset pool
        from src.database import get_next_word_bitset
        # Make this async to not block
        secret = await asyncio.to_thread(get_next_word_bitset, self.bot, interaction.guild.id, 'simple')
        self.race_session.secret = secret

        # Initialize games for ALL participants
        from src.game import WordleGame
        
        self.race_session.status = 'active'
        
        for user_id, user in self.race_session.participants.items():
            # Create game instance
            game = WordleGame(self.race_session.secret, 0, user, 0)
            self.race_session.race_games[user_id] = game
            self.race_session.green_scores[user_id] = 0
            
        # Launch monotonic timer task
        race_cog = self.bot.get_cog("RaceCommands")
        if race_cog:
            await race_cog.start_race_timer(self.race_session.channel_id, self.race_session)

        # Switch View to "Active Race" mode (Open Board button only)
        self.update_buttons()
        
        now = datetime.datetime.now()
        is_ended = now >= self.race_session.end_time if self.race_session.end_time else False
        timer_label = "Ended" if is_ended else "Ends"
        
        embed = discord.Embed(
            title="🏁 Race Started!",
            description=(
                f"**{self.race_session.participant_count}** racers are competing!\n"
                f"{timer_label} <t:{end_ts}:R>\n\n"
                "👇 **Click below to open your game board!**"
                "Note: If you dismiss the board, use `/show_race` to bring it back."
            ),
            color=discord.Color.green()
        )
        embed.set_footer(text="Good luck! The fastest solver wins!")
        
        await interaction.message.edit(embed=embed, view=self)

    async def open_board_button(self, interaction: discord.Interaction):
        """Ephemeral button to open the game board."""
        if not self.race_session.is_participant(interaction.user.id):
             return await interaction.response.send_message("⚠️ You are not in this race!", ephemeral=True)
        
        game = self.race_session.race_games.get(interaction.user.id)
        if not game:
            return await interaction.response.send_message("⚠️ Game not found.", ephemeral=True)
            
        # Create View and Embed
        view = RaceGameView(self.bot, game, interaction.user, self.race_session)
        
        filled = "●" * game.attempts_used
        empty = "○" * (game.max_attempts - game.attempts_used)
        progress_bar = f"[{filled}{empty}]"
        
        end_ts = int(self.race_session.end_time.timestamp()) if self.race_session.end_time else 0
        
        is_ended = int(time.time()) >= end_ts
        timer_label = "Ended" if is_ended else "Ends"
        
        embed = discord.Embed(title=f"🏁 Race Mode | Attempt {game.attempts_used}/{game.max_attempts}", color=discord.Color.gold())
        embed.description = (
            f"**Race against {self.race_session.participant_count} players!**\n"
            f"{timer_label} <t:{end_ts}:R>\n\n"
            f"Click **Make Guess** to start!"
        )
        if game.history:
             board_display = "\n".join([f"## {h['pattern']}" for h in game.history])
             embed.description += f"\n\n**Board:**\n{board_display}"
             
             # Keypad
             keypad = view.get_markdown_keypad(game.used_letters, interaction.user.id)
             embed.description += f"\n\n**Keyboard:**\n{keypad}"

        embed.set_footer(text=f"{game.max_attempts} tries left {progress_bar}")
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    
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
        # Calculate relative timestamp for lobby timeout
        timeout_ts = int(self.race_session.start_time.timestamp() + 300) # 5 mins
        
        embed = discord.Embed(
            title="🏁 Race Lobby",
            description=f"Waiting for racers... **{self.race_session.participant_count}** joined\nRace starts automatically or by host.",
            color=discord.Color.blue()
        )
        
        participants_list = "\n".join([f"• {user.display_name}" for user in self.race_session.participants.values()])
        embed.add_field(name="Participants", value=participants_list or "No one yet!", inline=False)
        embed.add_field(
            name="How to Play",
            value="• Click **Join Race** to participate\n"
                  "• At least **2 players** needed\n"
                  "• Starter clicks **Start** when ready\n"
                  "• Everyone races to solve the same word!\n"
                  "*Tip: Use `/help race` to understand delayed rewards!*",
            inline=False
        )
        
        embed.add_field(name="Lobby Timeout", value=f"<t:{timeout_ts}:R>", inline=False)
        
        return embed
    
    async def on_timeout(self):
        """Handle lobby timeout."""
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
                        description="The race lobby timed out.",
                        color=discord.Color.dark_grey()
                    )
                    await message.edit(embed=embed, view=None)
            except:
                pass

    async def update_to_ended(self):
        """Standardized update to change 'Ends' to 'Ended' in the lobby embed."""
        try:
            channel = self.bot.get_channel(self.race_session.channel_id)
            if not channel: return
            
            message = await channel.fetch_message(self.race_session.lobby_message_id)
            if not message: return
            
            embed = message.embeds[0]
            end_ts = int(self.race_session.end_time.timestamp())
            
            # Update description line from "Ends <t:ts:R>" to "Ended <t:ts:R>"
            new_desc = embed.description.replace("Ends <t:", "Ended <t:")
            embed.description = new_desc
            
            await message.edit(embed=embed)
        except Exception as e:
            # print(f"Error updating lobby to ended: {e}")
            pass


async def send_race_summary(bot, channel_id, race_session):
    """Helper to conclude race, generate summary, and send it."""
    # print(f"Attempting to send summary for channel {channel_id}...")
    
    # 1. Clean up session first (Atomic check)
    if not hasattr(bot, 'race_sessions') or channel_id not in bot.race_sessions:
        # print("⚠️ Race session already closed/deleted.")
        return
        
    del bot.race_sessions[channel_id]
    
    try:
        results = await asyncio.to_thread(race_session.conclude_race, bot)
        if not results:
            # print("⚠️ No results gathered for summary.")
            return

        # 2. Find the channel
        channel = bot.get_channel(channel_id)
        if not channel:
            try:
                channel = await bot.fetch_channel(channel_id)
            except:
                # print(f"❌ Failed to fetch channel {channel_id}")
                return

        # 3. Build Summary Embed
        word = race_session.secret.upper()
        
        # --- UI Sync: Update Lobby Message to "Ended" ---
        try:
            lobby_msg = await channel.fetch_message(race_session.lobby_message_id)
            if lobby_msg and lobby_msg.embeds:
                l_embed = lobby_msg.embeds[0]
                l_embed.description = l_embed.description.replace("Ends <t:", "Ended <t:")
                await lobby_msg.edit(embed=l_embed)
        except:
            pass

        embed = discord.Embed(
            title="🏁 Race Results 🏁",
            description=f"The word was **{word}**!",
            color=discord.Color.gold()
        )
        
        leaderboard_text = ""
        for res in results:
            medal = {1:"🥇", 2:"🥈", 3:"🥉"}.get(res['rank'], f"`{res['rank']}.`")
            user_name = res['user'].display_name
            status = "Solved" if res['won'] else "Failed"
            
            # Reward line
            rew = res.get('rewards', {})
            reward_text = rew.get('reward_text', 'N/A')
            
            leaderboard_text += f"{medal} **{user_name}** ({status} in {res['attempts']}/6)\n"
            leaderboard_text += (
                f"   > {reward_text} • "
                f"{'word found' if res['green_count'] == 5 else str(res['green_count']) + ' greens'}\n"
            )

        embed.add_field(name="Leaderboard", value=leaderboard_text or "No one completed the race.", inline=False)
        
        await channel.send(embed=embed)

        # 4. Personal Notifications (Streaks/Badges) - DISCONTINUED
        
        # print("✅ Summary sent successfully.")
        
    except Exception as e:
        print(f"❌ CRITICAL ERROR in send_race_summary: {e}")



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
        
        # Manual green count for tiebreaker
        green_count = sum(1 for i, c in enumerate(guess) if c.upper() == self.game.secret[i].upper())
        self.race_session.green_scores[interaction.user.id] = self.race_session.green_scores.get(interaction.user.id, 0) + green_count
        
        # Update the game display
        await interaction.response.defer()
        
        filled = "●" * self.game.attempts_used
        empty = "○" * (self.game.max_attempts - self.game.attempts_used)
        progress_bar = f"[{filled}{empty}]"
        
        board_display = "\n".join([f"# {h['pattern']}" for h in self.game.history])
        keypad = self.view_ref.get_markdown_keypad(self.game.used_letters, interaction.user.id)
        
        if won:
            # Record completion with time but NO REWARDS YET
            time_taken = (datetime.datetime.now() - self.game.start_time).total_seconds()
            self.race_session.record_completion(interaction.user.id, True, time_taken)
            
            embed = discord.Embed(title=f"✅ Word Solved! - Waiting for results...", color=discord.Color.green())
            embed.description = (
                f"You solved it in **{self.game.attempts_used}/{self.game.max_attempts}** tries!\n\n"
                f"**Final Board:**\n{board_display}\n\n"
                f"**Keyboard:**\n{keypad}"
            )
            embed.set_footer(text="Position and rewards will be revealed when everyone finishes.")
            
            self.view_ref.disable_all()
            await interaction.edit_original_response(content="", embed=embed, view=self.view_ref)
            
            # Check if all completed
            if self.race_session.all_completed:
                await send_race_summary(self.bot, self.race_session.channel_id, self.race_session)
        
        elif game_over:
            # Record completion as failed
            time_taken = (datetime.datetime.now() - self.game.start_time).total_seconds()
            self.race_session.record_completion(interaction.user.id, False, time_taken)
            
            embed = discord.Embed(title="💔 Out of Attempts", color=discord.Color.red())
            embed.description = (
                f"You ran out of tries!\n\n"
                f"**Final Board:**\n{board_display}\n\n"
                f"**Keyboard:**\n{keypad}"
            )
            
            # NO WORD REVEAL HERE (Prevent sharing)
            embed.set_footer(text="Waiting for others... Word revealed at end.")
            
            self.view_ref.disable_all()
            await interaction.edit_original_response(content="", embed=embed, view=self.view_ref)
            
            # Check if all completed
            if self.race_session.all_completed:
                await send_race_summary(self.bot, self.race_session.channel_id, self.race_session)
        
        else:
            # Continue playing
            end_ts = int(self.race_session.end_time.timestamp()) if self.race_session.end_time else 0
            is_ended = int(time.time()) >= end_ts
            timer_label = "Ended" if is_ended else "Ends"
            
            embed = discord.Embed(
                title=f"🏁 Race Mode | Attempt {self.game.attempts_used}/{self.game.max_attempts}",
                color=discord.Color.gold()
            )
            embed.description = (
                f"{timer_label} <t:{end_ts}:R>\n\n"
                f"**Board:**\n{board_display}\n\n"
                f"**Keyboard:**\n{keypad}"
            )
            embed.set_footer(text=f"{self.game.max_attempts - self.game.attempts_used} tries left {progress_bar}")
            
            await interaction.edit_original_response(content="", embed=embed, view=self.view_ref)


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
    
    @ui.button(label="Forfeit", style=discord.ButtonStyle.danger) # Renamed to Forfeit
    async def end_race_button(self, interaction: discord.Interaction, button: ui.Button):
        """End the race early (forfeit)."""
        # Record as failed
        time_taken = (datetime.datetime.now() - self.game.start_time).total_seconds()
        self.race_session.record_completion(interaction.user.id, False, time_taken)
        
        embed = discord.Embed(
            title="🏳️ Race Forfeited",
            description=f"You gave up. Waiting for others to finish...", # No word reveal
            color=discord.Color.dark_grey()
        )
        
        self.disable_all()
        await interaction.response.edit_message(embed=embed, view=self)
        
        # Check if everybody finished
        if self.race_session.all_completed:
             await send_race_summary(self.bot, self.race_session.channel_id, self.race_session)

    
    def get_markdown_keypad(self, used_letters: dict, user_id: int):
        """Generate markdown keyboard status matching Standard Game."""
        output_lines = []
        for row in KEYBOARD_LAYOUT:
             line = ""
             for char_key in row:
                 char = char_key.lower()
                 if char in used_letters['correct']:
                     emoji_key = f"{char}_correct" # Match utils.py format
                 elif char in used_letters['present']:
                     emoji_key = f"{char}_misplaced"
                 elif char in used_letters['absent']:
                     emoji_key = f"{char}_absent"
                 else:
                     emoji_key = f"{char}_unknown" # Or just char fallback
                
                 # Fetch emoji
                 emoji_display = EMOJIS.get(emoji_key, char_key)
                 line += emoji_display + " "
             output_lines.append(line.strip())
        
        # Add spacing indentation to match standard game
        if len(output_lines) >= 3:
             output_lines[1] = u"\u2007" + output_lines[1]
             output_lines[2] = u"\u2007\u2007\u2007\u2007" + output_lines[2]

        return "\n".join(output_lines)
