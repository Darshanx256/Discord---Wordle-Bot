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

    @discord.ui.button(label="Join", style=discord.ButtonStyle.primary)
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.game.participants.add(interaction.user.id)
        await interaction.response.send_message(f"You've joined the rush!", ephemeral=True)

    @discord.ui.button(label="Start", style=discord.ButtonStyle.success)
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
            'unlit': "https://cdn.discordapp.com/emojis/1456199350693789696.png"
        }

    @app_commands.command(name="word_rush", description="Fast-paced word hunt with linguistic constraints")
    @app_commands.guild_only()
    async def word_rush(self, interaction: discord.Interaction):
        cid = interaction.channel_id
        if cid in self.bot.constraint_games:
            return await interaction.response.send_message("A Word Rush session is already active in this channel.", ephemeral=True)
        
        if cid in self.bot.games or cid in self.bot.custom_games:
             return await interaction.response.send_message("A Wordle game is already active here. Finish it first.", ephemeral=True)

        game = ConstraintGame(self.bot, cid, interaction.user)
        self.bot.constraint_games[cid] = game
        
        # Clean lobby embed
        embed = discord.Embed(
            title="Word Rush",
            description=(
                "Find 5-letter words matching each constraint.\n"
                "Rounds last 10 seconds with traffic light timing.\n\n"
                "**Scoring**\n"
                "1st: 5 WR • 2nd: 4 WR • 3rd: 3 WR • Others: 1 WR\n\n"
                "**Rules**\n"
                "• No word can be used twice\n"
                "• Checkpoints every 12 rounds\n"
                "• Game ends after 3 rounds without guesses\n\n"
                f"Ready to play? Click **Join** below."
            ),
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Hosted by {interaction.user.display_name}")
        
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
                title="Rush Complete",
                description=f"Session MVP: **{mvp_name}** ({mvp_wr} WR)\n\nThanks for playing!",
                color=discord.Color.gold()
            )
            await interaction.response.send_message(embed=summary_embed)
        else:
            await interaction.response.send_message("Word Rush stopped.")
        
        self.bot.constraint_games.pop(cid, None)

    async def run_game_loop(self, interaction, game):
        try:
            channel = interaction.channel
            
            # Wait for start
            try:
                await asyncio.wait_for(game.start_confirmed.wait(), timeout=60)
            except asyncio.TimeoutError:
                if len(game.participants) < 1:
                    await channel.send("Rush cancelled: no participants joined.")
                    self.bot.constraint_games.pop(game.channel_id, None)
                    return
            
            # Countdown
            countdown_embed = discord.Embed(
                title="Starting...",
                description=f"{len(game.participants)} player{'s' if len(game.participants) > 1 else ''} ready",
                color=discord.Color.blue()
            )
            
            try:
                await game.game_msg.edit(embed=countdown_embed, view=None)
            except:
                game.game_msg = await channel.send(embed=countdown_embed)

            await asyncio.sleep(2)
            
            try:
                await game.game_msg.delete()
            except:
                pass

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
                visual = game.active_puzzle.get('visual', '')
                
                # Create round embed with fixed structure
                round_embed = discord.Embed(
                    title=f"Round {game.round_number}",
                    description=puzzle_desc + (f"\n\n{visual}" if visual else ""),
                    color=discord.Color.green()
                )
                round_embed.set_thumbnail(url=self.signal_urls['green'])
                
                msg = await channel.send(embed=round_embed)
                game.game_msg = msg
                game.is_round_active = True
                
                try:
                    # Green: 4s
                    await asyncio.sleep(4)
                    round_embed.set_thumbnail(url=self.signal_urls['yellow'])
                    round_embed.color = discord.Color.gold()
                    await msg.edit(embed=round_embed)
                    
                    # Yellow: 3s
                    await asyncio.sleep(3)
                    round_embed.set_thumbnail(url=self.signal_urls['red'])
                    round_embed.color = discord.Color.red()
                    await msg.edit(embed=round_embed)
                    
                    # Red: 3s
                    await asyncio.sleep(3)
                    
                except asyncio.CancelledError:
                    break
                
                # End round
                game.is_round_active = False
                
                # Update to unlit state
                round_embed.set_thumbnail(url=self.signal_urls['unlit'])
                round_embed.color = discord.Color.dark_gray()
                
                if not game.winners_in_round:
                    round_embed.description = puzzle_desc + (f"\n\n{visual}" if visual else "") + "\n\n*No correct guesses*"
                
                await msg.edit(embed=round_embed)
                
                # Brief pause to see results
                await asyncio.sleep(2)
                try:
                    await msg.delete()
                except:
                    pass
                
                # Check failure condition
                if not game.winners_in_round:
                    game.rounds_without_guess += 1
                else:
                    game.rounds_without_guess = 0
                
                if game.rounds_without_guess >= 3:
                    final_embed = discord.Embed(
                        title="Game Over",
                        description="Three rounds without correct guesses.",
                        color=discord.Color.dark_red()
                    )
                    
                    if game.total_wr_per_user:
                        sorted_mvp = sorted(game.total_wr_per_user.items(), key=lambda x: x[1], reverse=True)
                        m_id, m_wr = sorted_mvp[0]
                        m_name = await get_cached_username(self.bot, m_id)
                        final_embed.description += f"\n\nSession MVP: **{m_name}** ({m_wr} WR)"

                    await channel.send(embed=final_embed)
                    game.is_running = False
                    break
                
                # Buffer between rounds
                await asyncio.sleep(1.5)

        except Exception as e:
            print(f"Error in Rush Loop: {e}")
        finally:
            self.bot.constraint_games.pop(interaction.channel_id, None)

    async def show_checkpoint(self, channel, game):
        checkpoint_embed = discord.Embed(
            title="Checkpoint",
            description="Calculating scores...",
            color=discord.Color.blue()
        )
        msg = await channel.send(embed=checkpoint_embed)
        
        sorted_scores = sorted(game.scores.items(), key=lambda x: x[1]['wr'], reverse=True)
        
        if not sorted_scores:
            checkpoint_embed.description = "No scores to report.\n\nContinuing..."
            await msg.edit(embed=checkpoint_embed)
            await asyncio.sleep(5)
            try:
                await msg.delete()
            except:
                pass
            return

        # Award scores
        lines = []
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
            
            rank = ""
            if i == 0:
                rank = "1st"
            elif i == 1:
                rank = "2nd"
            elif i == 2:
                rank = "3rd"
            else:
                rank = f"{i+1}th"
            
            lines.append(f"{rank} • **{user_name}** — {wr_total} WR ({rounds} rounds)")

        checkpoint_embed.description = "\n".join(lines) + "\n\nResuming in 10 seconds..."
        checkpoint_embed.color = discord.Color.green()
        await msg.edit(embed=checkpoint_embed)
        
        # Reset scores
        game.scores = {}
        
        await asyncio.sleep(10)
        try:
            await msg.delete()
        except:
            pass

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
        
        game.add_score(message.author.id, wr_gain)
        
        try:
            await message.add_reaction(reaction)
        except:
            pass

async def setup(bot):
    await bot.add_cog(ConstraintMode(bot))
