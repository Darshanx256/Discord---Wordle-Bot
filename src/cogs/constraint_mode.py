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
    def __init__(self, bot, channel_id):
        self.bot = bot
        self.channel_id = channel_id
        self.round_number = 0
        self.scores = {} # user_id: {wr: 0, rounds_won: 0}
        self.rounds_without_guess = 0
        self.active_puzzle = None
        self.used_words = set()
        self.winners_in_round = [] # list of user_ids in order
        self.is_running = True
        self.is_round_active = False # New flag
        self.generator = ConstraintGenerator(bot.valid_set)
        self.round_task = None
        self.game_msg = None

    def add_score(self, user_id, wr_gain):
        if user_id not in self.scores:
            self.scores[user_id] = {'wr': 0, 'rounds_won': 0}
        self.scores[user_id]['wr'] += wr_gain
        self.scores[user_id]['rounds_won'] += 1

class ConstraintMode(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def get_signal_emoji(self, name):
        """Helper to get signal emojis from cache or default."""
        return EMOJIS.get(f"signal_{name}", "🚥")

    @app_commands.command(name="word_rush", description="Fast-paced word hunt with linguistic constraints!")
    @app_commands.guild_only()
    async def word_rush(self, interaction: discord.Interaction):
        """Starts the Constraint Mode (Word Rush)."""
        cid = interaction.channel_id
        if cid in self.bot.constraint_games:
            return await interaction.response.send_message("⚠️ A Word Rush session is already active in this channel!", ephemeral=True)
        
        if cid in self.bot.games or cid in self.bot.custom_games:
             return await interaction.response.send_message("⚠️ A Wordle game is already active here. Finish it first!", ephemeral=True)

        game = ConstraintGame(self.bot, cid)
        self.bot.constraint_games[cid] = game
        
        await interaction.response.send_message("🚀 **CONSTRAINT MODE IS ABOUT TO START!**", ephemeral=True)
        
        # Start the game loop
        asyncio.create_task(self.run_game_loop(interaction, game))

    @app_commands.command(name="stop_rush", description="Stop the active Word Rush session.")
    @app_commands.guild_only()
    async def stop_rush(self, interaction: discord.Interaction):
        cid = interaction.channel_id
        if cid not in self.bot.constraint_games:
            return await interaction.response.send_message("No active Word Rush session here.", ephemeral=True)
        
        game = self.bot.constraint_games[cid]
        game.is_running = False
        if game.round_task:
            game.round_task.cancel()
        
        self.bot.constraint_games.pop(cid, None)
        await interaction.response.send_message("🛑 **Word Rush has been stopped!**")

    async def run_game_loop(self, interaction, game):
        try:
            channel = interaction.channel
            
            # 1. INITIAL WAIT (10s)
            embed = discord.Embed(title="🚥 PREPARING RUSH...", color=discord.Color.red())
            embed.set_image(url="attachment://signal.png") # Not actually attachment, we'll use emoji in desc for size
            
            # Using huge emoji in description for visibility
            def build_signal_embed(color_name, seconds_left):
                e = discord.Embed(title="🚥 GET READY!", color=getattr(discord.Color, color_name)())
                e.description = f"# {self.get_signal_emoji(color_name)}\nStarting in **{seconds_left}s**..."
                return e

            msg = await channel.send(embed=build_signal_embed("red", 10))
            game.game_msg = msg
            
            # 3s Red
            await asyncio.sleep(3)
            await msg.edit(embed=build_signal_embed("yellow", 7))
            # 3s Yellow
            await asyncio.sleep(3)
            await msg.edit(embed=build_signal_embed("green", 4))
            # 4s Green
            await asyncio.sleep(4)
            
            # Change to unlit
            unlit_embed = discord.Embed(title="🟢 GO!", color=discord.Color.green())
            unlit_embed.description = f"# {self.get_signal_emoji('unlit')}\nRound 1 starting..."
            await msg.edit(embed=unlit_embed)
            await asyncio.sleep(1)

            # 2. MAIN ROUND LOOP
            while game.is_running:
                game.round_number += 1
                game.winners_in_round = []
                
                # Check for checkpoint
                if game.round_number > 1 and (game.round_number - 1) % 12 == 0:
                    await self.show_checkpoint(channel, game)
                    if not game.is_running: break

                # Generate Puzzle
                game.active_puzzle = game.generator.generate_puzzle()
                
                # Round Start Embed
                puzzle_desc = game.active_puzzle['description']
                visual = game.active_puzzle['visual']
                
                round_embed = discord.Embed(title=f"ROUND {game.round_number}", color=discord.Color.green())
                desc = f"# {self.get_signal_emoji('green')}\n\n{puzzle_desc}"
                if visual:
                    desc += f"\n\n{visual}"
                round_embed.description = desc
                round_embed.set_footer(text="Wait: 🟢 Green -> 🟡 Yellow -> 🔴 Red")
                
                await msg.edit(embed=round_embed)
                
                # Round Timer (10s)
                game.is_round_active = True
                
                try:
                    # 4s Green
                    await asyncio.sleep(4)
                    round_embed.description = f"# {self.get_signal_emoji('yellow')}\n\n{puzzle_desc}\n\n**HURRY UP!**"
                    if visual: round_embed.description += f"\n\n{visual}"
                    round_embed.color = discord.Color.gold()
                    await msg.edit(embed=round_embed)
                    
                    # 3s Yellow
                    await asyncio.sleep(3)
                    round_embed.description = f"# {self.get_signal_emoji('red')}\n\n{puzzle_desc}\n\n**LAST CHANCE!**"
                    if visual: round_embed.description += f"\n\n{visual}"
                    round_embed.color = discord.Color.red()
                    await msg.edit(embed=round_embed)
                    
                    # 3s Red
                    await asyncio.sleep(3)
                except asyncio.CancelledError:
                    break
                
                # End Round
                game.is_round_active = False
                round_embed.description = f"# {self.get_signal_emoji('unlit')}\nRound Ended!\n\nUse `/stop_rush` to stop."
                round_embed.color = discord.Color.dark_gray()
                await msg.edit(embed=round_embed)
                
                # Check for game over (no guesses for 3 rounds)
                if not game.winners_in_round:
                    game.rounds_without_guess += 1
                else:
                    game.rounds_without_guess = 0
                
                if game.rounds_without_guess >= 3:
                    await channel.send("💀 **GAME OVER!** No correct guesses for 3 rounds.")
                    game.is_running = False
                    break
                
                # Buffer between rounds
                await asyncio.sleep(2)

        except Exception as e:
            print(f"Error in Rush Loop: {e}")
        finally:
            self.bot.constraint_games.pop(interaction.channel_id, None)

    async def show_checkpoint(self, channel, game):
        """Shows scores and persists them to DB."""
        checkpoint_embed = discord.Embed(title="🚥 CHECKPOINT REACHED!", color=discord.Color.blue())
        checkpoint_embed.description = f"# {self.get_signal_emoji('red')}\nRanking participants..."
        msg = await channel.send(embed=checkpoint_embed)
        
        # Rankings by total WR accumulated in this checkpoint
        sorted_scores = sorted(game.scores.items(), key=lambda x: x[1]['wr'], reverse=True)
        
        if not sorted_scores:
            checkpoint_embed.description += "\n\nNo participants this checkpoint."
            await msg.edit(embed=checkpoint_embed)
            await asyncio.sleep(5)
            return

        # Distribute rewards in DB
        lines = []
        for i, (uid, data) in enumerate(sorted_scores):
            user_name = await get_cached_username(self.bot, uid)
            wr_total = data['wr']
            rounds = data['rounds_won']
            
            # Persist to DB
            try:
                # Base XP for checkpoint participation + bonus for rank
                xp_gain = 25 + max(0, 25 - (i * 5))
                
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
            
            medal = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else "🔹"
            lines.append(f"{medal} **{user_name}**: +{wr_total} WR | {rounds} rounds")

        checkpoint_embed.add_field(name="Rankings (Last 12 Rounds)", value="\n".join(lines), inline=False)
        checkpoint_embed.description = f"# {self.get_signal_emoji('green')}\nRush will continue soon!"
        checkpoint_embed.color = discord.Color.green()
        await msg.edit(embed=checkpoint_embed)
        
        # Reset ephemeral scores
        game.scores = {}
        
        await asyncio.sleep(10) # 10s wait as requested for "mudae style" flow

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot: return
        cid = message.channel.id
        if cid not in self.bot.constraint_games: return
        
        game = self.bot.constraint_games[cid]
        if not game.is_round_active or not game.active_puzzle: return
        
        content = message.content.strip().lower()
        if len(content) != 5 or not content.isalpha(): return
        
        # Word cannot be reused in the whole game session
        if content in game.used_words:
            return

        # Word must be in dictionary
        if content not in self.bot.valid_set: return
        
        # Word must satisfy constraints
        if content not in game.active_puzzle['solutions']:
            return

        # Valid Guess!
        game.used_words.add(content)
        
        # Mark winner rank
        rank = len(game.winners_in_round) + 1
        game.winners_in_round.append(message.author.id)
        
        # Score calculation: 1st (5), 2nd (4), 3rd (3), 4th (2), rest (1)
        wr_gain = 1
        reaction = "👍"
        
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
            # No specific emoji for 4th mentioned, but prompt says "followed by second and third, further bot just likes"
            # So 4th is a "like" 👍
        
        game.add_score(message.author.id, wr_gain)
        
        try:
            await message.add_reaction(reaction)
        except: pass

async def setup(bot):
    await bot.add_cog(ConstraintMode(bot))
