import asyncio
import time
import datetime
import random
import discord
from discord.ext import commands
from discord import app_commands
from src.mechanics.constraint_logic import ConstraintGenerator
from src.utils import EMOJIS, get_cached_username
from src.database import fetch_user_profile_v2

class ConstraintGame:
    def __init__(self, bot, channel_id, started_by):
        self.bot = bot
        self.channel_id = channel_id
        self.started_by = started_by
        self.round_number = 0
        self.scores = {}
        self.rounds_without_guess = 0
        self.active_puzzle = None
        self.used_words = set()
        self.winners_in_round = []
        self.is_running = True
        self.is_round_active = False
        self.generator = ConstraintGenerator(bot.valid_set)
        self.round_task = None
        self.game_msg = None
        self.participants = {started_by.id}
        self.start_confirmed = asyncio.Event()
        self.total_wr_per_user = {}

    def add_score(self, user_id, wr_gain):
        if user_id not in self.scores:
            self.scores[user_id] = {'wr': 0, 'rounds_won': 0}
        self.scores[user_id]['wr'] += wr_gain
        self.scores[user_id]['rounds_won'] += 1

class RushStartView(discord.ui.View):
    def __init__(self, game):
        super().__init__(timeout=60)
        self.game = game

    @discord.ui.button(label="Join Rush", style=discord.ButtonStyle.primary, emoji="⚡")
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.game.participants.add(interaction.user.id)
        await interaction.response.send_message(f"You've joined the rush!", ephemeral=True)

    @discord.ui.button(label="Start Game", style=discord.ButtonStyle.success, emoji="▶️")
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.game.started_by.id and not interaction.user.guild_permissions.manage_messages:
            return await interaction.response.send_message("Only the host or an admin can start the game.", ephemeral=True)
        
        self.game.start_confirmed.set()
        await interaction.response.defer()
        self.stop()

class ConstraintMode(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.signal_urls = {
            'green': "https://cdn.discordapp.com/emojis/1456199435682975827.png",
            'yellow': "https://cdn.discordapp.com/emojis/1456199439277494418.png",
            'red': "https://cdn.discordapp.com/emojis/1456199431803244624.png",
            'unlit': "https://cdn.discordapp.com/emojis/1456199350693789696.png",
            'checkpoint': "https://cdn.discordapp.com/emojis/1456313204597588101.png"
        }

    @app_commands.command(name="word_rush", description="Fast-paced word hunt with linguistic constraints")
    @app_commands.guild_only()
    async def word_rush(self, interaction: discord.Interaction):
        cid = interaction.channel_id
        if cid in self.bot.constraint_games:
            return await interaction.response.send_message("⚠️ A Word Rush session is already active in this channel.", ephemeral=True)
        
        if cid in self.bot.games or cid in self.bot.custom_games:
             return await interaction.response.send_message("⚠️ A Wordle game is already active here. Finish it first!", ephemeral=True)

        game = ConstraintGame(self.bot, cid, interaction.user)
        self.bot.constraint_games[cid] = game
        
        # Gorgeous lobby embed
        embed = discord.Embed(
            title="⚡ Word Rush",
            description=(
                "Find 5-letter words matching each constraint.\n"
                "Each round lasts **12 seconds** with traffic light timing.\n\n"
                "**🎯 Scoring**\n"
                "```\n"
                "1st place  →  5 WR\n"
                "2nd place  →  4 WR\n"
                "3rd place  →  3 WR\n"
                "4th place  →  2 WR\n"
                "Others     →  1 WR\n"
                "```\n"
                "**📋 Rules**\n"
                "• No word can be used twice in a session\n"
                "• Rewards distributed every 12 rounds\n"
                "• Game ends after 5 rounds without guesses\n\n"
                "Ready to test your vocabulary? Join below!"
            ),
            color=discord.Color.from_rgb(88, 101, 242)
        )
        embed.set_thumbnail(url=self.signal_urls['unlit'])
        embed.set_footer(text=f"🎮 Hosted by {interaction.user.display_name}")
        
        view = RushStartView(game)
        await interaction.response.send_message(embed=embed, view=view)
        lobby_msg = await interaction.original_response()
        game.game_msg = lobby_msg
        
        asyncio.create_task(self.run_game_loop(interaction, game))

    @app_commands.command(name="stop_rush", description="Stop the active Word Rush session")
    @app_commands.guild_only()
    async def stop_rush(self, interaction: discord.Interaction):
        cid = interaction.channel_id
        if cid not in self.bot.constraint_games:
            return await interaction.response.send_message("No active Word Rush session here.", ephemeral=True)
        
        game = self.bot.constraint_games[cid]
        game.is_running = False
        if game.round_task:
            game.round_task.cancel()
        
        if game.total_wr_per_user:
            sorted_mvp = sorted(game.total_wr_per_user.items(), key=lambda x: x[1], reverse=True)
            mvp_id, mvp_wr = sorted_mvp[0]
            mvp_name = await get_cached_username(self.bot, mvp_id)
            
            summary_embed = discord.Embed(
                title="🏆 Rush Complete",
                description=f"**Session MVP**\n{mvp_name} • {mvp_wr} WR\n\nThanks for playing!",
                color=discord.Color.gold()
            )
            await interaction.response.send_message(embed=summary_embed)
        else:
            await interaction.response.send_message("🛑 Word Rush stopped.")
        
        self.bot.constraint_games.pop(cid, None)

    def format_visual_pattern(self, visual):
        """Convert text pattern to emoji blocks."""
        if not visual:
            return ""
        
        # Process each line
        lines = visual.split('\n')
        formatted_lines = []
        
        for line in lines:
            formatted = ""
            for char in line:
                char_low = char.lower()
                if char_low.isalpha():
                    # Use green block for known letters
                    formatted += EMOJIS.get(f"block_{char_low}_green", "")
                elif char == '-':
                    # Use grey block for unknown positions
                    formatted += "⬜"
                else:
                    # Keep other characters (spaces, etc.)
                    formatted += char
            formatted_lines.append(formatted)
        
        return '\n'.join(formatted_lines)

    async def run_game_loop(self, interaction, game):
        try:
            channel = interaction.channel
            
            # Wait for start
            try:
                await asyncio.wait_for(game.start_confirmed.wait(), timeout=60)
            except asyncio.TimeoutError:
                if len(game.participants) < 1:
                    await channel.send("⏰ Rush cancelled: no participants joined in time.")
                    self.bot.constraint_games.pop(game.channel_id, None)
                    return
            
            # Beautiful countdown sequence
            countdown_embed = discord.Embed(
                title="Starting Rush",
                description=f"**{len(game.participants)}** player{'s' if len(game.participants) > 1 else ''} ready",
                color=discord.Color.from_rgb(220, 20, 60)
            )
            countdown_embed.set_thumbnail(url=self.signal_urls['red'])
            
            try:
                await game.game_msg.edit(embed=countdown_embed, view=None)
            except:
                game.game_msg = await channel.send(embed=countdown_embed)

            await asyncio.sleep(1.2)
            countdown_embed.set_thumbnail(url=self.signal_urls['yellow'])
            countdown_embed.color = discord.Color.gold()
            await game.game_msg.edit(embed=countdown_embed)
            
            await asyncio.sleep(1.2)
            countdown_embed.set_thumbnail(url=self.signal_urls['green'])
            countdown_embed.description = f"**{len(game.participants)}** player{'s' if len(game.participants) > 1 else ''} ready\n\n**GO!**"
            countdown_embed.color = discord.Color.green()
            countdown_embed.title = "GO!"
            await game.game_msg.edit(embed=countdown_embed)
            
            await asyncio.sleep(1.5)
            
            # Turn off the light
            countdown_embed.set_thumbnail(url=self.signal_urls['unlit'])
            countdown_embed.color = discord.Color.dark_gray()
            await game.game_msg.edit(embed=countdown_embed)
            
            await asyncio.sleep(0.5)

            # Main round loop
            while game.is_running:
                game.round_number += 1
                game.winners_in_round = []
                
                # Checkpoint check
                if game.round_number > 1 and (game.round_number - 1) % 12 == 0:
                    await self.show_checkpoint(channel, game)
                    if not game.is_running:
                        break

                # Generate puzzle
                game.active_puzzle = game.generator.generate_puzzle()
                puzzle_desc = game.active_puzzle['description']
                visual_raw = game.active_puzzle.get('visual', '')
                visual = self.format_visual_pattern(visual_raw)
                
                # Determine if this is a pattern puzzle (has visual blocks)
                has_pattern = bool(visual)
                round_duration = 20 if has_pattern else 12
                
                # Create round embed with fixed structure - show only emoji pattern if available
                display_text = visual if visual else puzzle_desc
                
                round_embed = discord.Embed(
                    title=f"Round {game.round_number}",
                    description=display_text,
                    color=discord.Color.green()
                )
                round_embed.set_thumbnail(url=self.signal_urls['green'])
                round_embed.set_footer(text=f"{round_duration} seconds • Type your answer!")
                
                msg = await channel.send(embed=round_embed)
                game.game_msg = msg
                game.is_round_active = True
                
                try:
                    if has_pattern:
                        # Pattern puzzle: 20 seconds (8-7-5)
                        await asyncio.sleep(8)
                        round_embed.set_thumbnail(url=self.signal_urls['yellow'])
                        round_embed.color = discord.Color.gold()
                        round_embed.set_footer(text="12 seconds left")
                        await msg.edit(embed=round_embed)
                        
                        await asyncio.sleep(7)
                        round_embed.set_thumbnail(url=self.signal_urls['red'])
                        round_embed.color = discord.Color.red()
                        round_embed.set_footer(text="5 seconds left!")
                        await msg.edit(embed=round_embed)
                        
                        await asyncio.sleep(5)
                    else:
                        # Regular puzzle: 12 seconds (5-4-3)
                        await asyncio.sleep(5)
                        round_embed.set_thumbnail(url=self.signal_urls['yellow'])
                        round_embed.color = discord.Color.gold()
                        round_embed.set_footer(text="7 seconds left")
                        await msg.edit(embed=round_embed)
                        
                        await asyncio.sleep(4)
                        round_embed.set_thumbnail(url=self.signal_urls['red'])
                        round_embed.color = discord.Color.red()
                        round_embed.set_footer(text="3 seconds left!")
                        await msg.edit(embed=round_embed)
                        
                        await asyncio.sleep(3)
                    
                except asyncio.CancelledError:
                    break
                
                # End round
                game.is_round_active = False
                
                # Update to unlit state with results
                round_embed.set_thumbnail(url=self.signal_urls['unlit'])
                round_embed.color = discord.Color.dark_gray()
                
                if game.winners_in_round:
                    winners_count = len(game.winners_in_round)
                    round_embed.set_footer(text=f"✓ {winners_count} correct guess{'es' if winners_count > 1 else ''}")
                else:
                    round_embed.set_footer(text="✗ No correct guesses!")
                
                await msg.edit(embed=round_embed)
                
                # Check failure condition
                if not game.winners_in_round:
                    game.rounds_without_guess += 1
                else:
                    game.rounds_without_guess = 0
                
                if game.rounds_without_guess >= 5:
                    final_embed = discord.Embed(
                        title="💀 Game Over",
                        description="Five consecutive rounds without correct guesses.",
                        color=discord.Color.dark_red()
                    )
                    
                    if game.total_wr_per_user:
                        sorted_mvp = sorted(game.total_wr_per_user.items(), key=lambda x: x[1], reverse=True)
                        m_id, m_wr = sorted_mvp[0]
                        m_name = await get_cached_username(self.bot, m_id)
                        final_embed.add_field(
                            name="🏆 Session MVP",
                            value=f"**{m_name}**\n{m_wr} WR earned",
                            inline=False
                        )

                    await channel.send(embed=final_embed)
                    game.is_running = False
                    break
                
                # Buffer between rounds
                await asyncio.sleep(2)

        except Exception as e:
            print(f"Error in Rush Loop: {e}")
        finally:
            self.bot.constraint_games.pop(interaction.channel_id, None)

    async def show_checkpoint(self, channel, game):
        checkpoint_embed = discord.Embed(
            title="Checkpoint",
            description="Calculating scores and distributing rewards...",
            color=discord.Color.blue()
        )
        checkpoint_embed.set_thumbnail(url=self.signal_urls['checkpoint'])
        msg = await channel.send(embed=checkpoint_embed)
        
        sorted_scores = sorted(game.scores.items(), key=lambda x: x[1]['wr'], reverse=True)
        
        if not sorted_scores:
            checkpoint_embed.description = "No scores to report this checkpoint.\n\nContinuing in 5 seconds..."
            await msg.edit(embed=checkpoint_embed)
            await asyncio.sleep(5)
            return

        # Award scores
        lines = []
        medals = ["🥇", "🥈", "🥉"]
        
        for i, (uid, data) in enumerate(sorted_scores):
            user_name = await get_cached_username(self.bot, uid)
            wr_total = data['wr']
            rounds = data['rounds_won']
            
            game.total_wr_per_user[uid] = game.total_wr_per_user.get(uid, 0) + wr_total
            
            try:
                xp_gain = 35 + max(0, 30 - (i * 5))
                
                self.bot.supabase_client.rpc('record_game_result_v4', {
                    'p_user_id': uid,
                    'p_guild_id': channel.guild.id if channel.guild else None,
                    'p_mode': 'MULTI',
                    'p_xp_gain': xp_gain,
                    'p_wr_delta': wr_total,
                    'p_is_win': (i == 0),
                    'p_egg_trigger': None
                }).execute()
            except Exception as e:
                print(f"Failed to record checkpoint for {uid}: {e}")
            
            medal = medals[i] if i < 3 else "▫️"
            lines.append(f"{medal} **{user_name}** • {wr_total} WR • {rounds} rounds")

        checkpoint_embed.description = "\n".join(lines) + "\n\n*Resuming in 8 seconds...*"
        checkpoint_embed.color = discord.Color.green()
        checkpoint_embed.set_thumbnail(url=self.signal_urls['green'])
        await msg.edit(embed=checkpoint_embed)
        
        # Reset scores
        game.scores = {}
        
        await asyncio.sleep(8)

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        if user.bot:
            return
        cid = reaction.message.channel.id
        if cid not in self.bot.constraint_games:
            return
        
        game = self.bot.constraint_games[cid]
        if game.game_msg and reaction.message.id == game.game_msg.id:
            if not game.start_confirmed.is_set():
                game.participants.add(user.id)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return
        cid = message.channel.id
        if cid not in self.bot.constraint_games:
            return
        
        game = self.bot.constraint_games[cid]
        if not game.is_round_active or not game.active_puzzle:
            return
        
        content = message.content.strip().lower()
        if len(content) != 5 or not content.isalpha():
            return
        
        if content in game.used_words:
            return
        if content not in self.bot.valid_set:
            return
        if content not in game.active_puzzle['solutions']:
            return

        # Valid guess
        game.used_words.add(content)
        game.participants.add(message.author.id)
        
        rank = len(game.winners_in_round) + 1
        game.winners_in_round.append(message.author.id)
        
        # Score calculation
        wr_gain = 1
        reaction = "✓"
        
        if rank == 1:
            wr_gain = 5
            reaction = "🥇"
        elif rank == 2:
            wr_gain = 4
            reaction = "🥈"
        elif rank == 3:
            wr_gain = 3
            reaction = "🥉"
        elif rank == 4:
            wr_gain = 2
            reaction = "⭐"
        
        game.add_score(message.author.id, wr_gain)
        
        try:
            await message.add_reaction(reaction)
        except:
            pass

async def setup(bot):
    await bot.add_cog(ConstraintMode(bot))
