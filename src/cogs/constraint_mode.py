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
        self.user_answers_this_round = {}  # Track answers per user per round
        self.is_running = True
        self.is_round_active = False
        self.generator = ConstraintGenerator(bot.valid_set, bot.full_dict)
        self.round_task = None
        self.game_msg = None
        self.participants = {started_by.id}
        self.start_confirmed = asyncio.Event()
        self.total_wr_per_user = {}
        self.puzzle_types_used = set()  # Track which puzzle types have been used
        self.rounds_since_last_bonus = 0
        self.is_bonus_round = False
        self.bonus_collected_words = {}  # For bonus rounds tracking multiple words

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
            'checkpoint': "https://cdn.discordapp.com/emojis/1456313204597588101.png",
            'unknown': "https://cdn.discordapp.com/emojis/1456488648923938846.png",
            'bonus': "https://cdn.discordapp.com/emojis/1456488648923938846.png"  # Can use custom bonus emoji
        }

    @app_commands.command(name="word_rush", description="Fast-paced word hunt with linguistic constraints")
    @app_commands.guild_only()
    async def word_rush(self, interaction: discord.Interaction):
        cid = interaction.channel_id
        if cid in self.bot.constraint_mode:
            return await interaction.response.send_message("⚠️ A Word Rush session is already active in this channel.", ephemeral=True)
        
        if cid in self.bot.games or cid in self.bot.custom_games:
             return await interaction.response.send_message("⚠️ A Wordle game is already active here. Finish it first!", ephemeral=True)

        game = ConstraintGame(self.bot, cid, interaction.user)
        self.bot.constraint_mode[cid] = game
        
        embed = discord.Embed(
            title="⚡ Word Rush",
            description=(
                "Find words matching each linguistic constraint!\n"
                "Watch the traffic lights for timing guidance.\n\n"
                "**🎯 Scoring**\n"
                "```\n"
                "1st place  →  5 Rush Points\n"
                "2nd place  →  4 Rush Points\n"
                "3rd place  →  3 Rush Points\n"
                "4th place  →  2 Rush Points\n"
                "Others     →  1 Rush Point\n"
                "```\n"
                "**📋 Rules**\n"
                "• No word reuse in same session\n"
                "• **Rush Points** converted to WR at checkpoints\n"
                "• Game ends after 5 rounds without guesses\n"
                "• 🎁 Random bonus rounds with 3x rewards!\n\n"
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
        if cid not in self.bot.constraint_mode:
            return await interaction.response.send_message("No active Word Rush session here.", ephemeral=True)
        
        game = self.bot.constraint_mode[cid]
        game.is_running = False
        if game.round_task:
            game.round_task.cancel()
        
        if game.total_wr_per_user:
            sorted_mvp = sorted(game.total_wr_per_user.items(), key=lambda x: x[1], reverse=True)
            mvp_id, mvp_wr = sorted_mvp[0]
            mvp_name = await get_cached_username(self.bot, mvp_id)
            
            summary_embed = discord.Embed(
                title="🏆 Rush Complete",
                description=f"**Session MVP**\n{mvp_name} • {mvp_wr} Rush Points\n\nThanks for playing!",
                color=discord.Color.gold()
            )
            await interaction.response.send_message(embed=summary_embed)
        else:
            await interaction.response.send_message("🛑 Word Rush stopped.")
        
        self.bot.constraint_mode.pop(cid, None)

    def format_visual_pattern(self, visual):
        """Convert text pattern to emoji blocks."""
        if not visual:
            return ""
        
        lines = visual.split('\n')
        formatted_lines = []
        
        for line in lines:
            formatted = ""
            for char in line:
                char_low = char.lower()
                if char_low.isalpha():
                    # Use custom emoji format: :green_X:
                    formatted += f":green_{char.upper()}:"
                elif char == '-':
                    emoji_id = self.signal_urls['unknown'].split('/')[-1].replace('.png', '')
                    formatted += f"<:unknown:{emoji_id}>"
                else:
                    formatted += char
            formatted_lines.append(formatted)
        
        return '\n'.join(formatted_lines)

    async def run_game_loop(self, interaction, game):
        try:
            channel = interaction.channel
            
            try:
                await asyncio.wait_for(game.start_confirmed.wait(), timeout=60)
            except asyncio.TimeoutError:
                if len(game.participants) < 1:
                    await channel.send("⏰ Rush cancelled: no participants joined in time.")
                    self.bot.constraint_mode.pop(game.channel_id, None)
                    return
            
            # Countdown sequence with consistent formatting
            countdown_embed = discord.Embed(
                title="⚡ Word Rush Starting",
                description="🔴 **READY?**\n\n\u200b\n\u200b\n\u200b",
                color=discord.Color.from_rgb(220, 20, 60)
            )
            countdown_embed.set_thumbnail(url=self.signal_urls['red'])
            
            try:
                await game.game_msg.edit(embed=countdown_embed, view=None)
            except:
                game.game_msg = await channel.send(embed=countdown_embed)

            await asyncio.sleep(1.2)
            countdown_embed.set_thumbnail(url=self.signal_urls['yellow'])
            countdown_embed.description = "🟡 **GET SET!**\n\n\u200b\n\u200b\n\u200b"
            countdown_embed.color = discord.Color.gold()
            await game.game_msg.edit(embed=countdown_embed)
            
            await asyncio.sleep(1.2)
            countdown_embed.set_thumbnail(url=self.signal_urls['green'])
            countdown_embed.description = "🟢 **GO!**\n\n\u200b\n\u200b\n\u200b"
            countdown_embed.color = discord.Color.green()
            await game.game_msg.edit(embed=countdown_embed)
            
            await asyncio.sleep(1.5)
            
            countdown_embed.set_thumbnail(url=self.signal_urls['unlit'])
            countdown_embed.color = discord.Color.dark_gray()
            await game.game_msg.edit(embed=countdown_embed)
            
            await asyncio.sleep(0.5)

            # Main round loop
            while game.is_running:
                game.round_number += 1
                game.winners_in_round = []
                game.user_answers_this_round = {}
                game.bonus_collected_words = {}
                game.rounds_since_last_bonus += 1
                
                # Check if it's time for checkpoint
                if game.round_number > 1 and (game.round_number - 1) % 12 == 0:
                    await self.show_checkpoint(channel, game)
                    if not game.is_running:
                        break
                
                # Determine if this is a bonus round (random chance after 15+ rounds)
                game.is_bonus_round = (game.rounds_since_last_bonus >= 15 and 
                                      random.random() < 0.15 and 
                                      len(game.participants) > 0)
                
                if game.is_bonus_round:
                    game.rounds_since_last_bonus = 0
                
                # Ensure puzzle variety every 20 rounds
                force_unused_type = (game.round_number % 20 == 0 and 
                                    len(game.puzzle_types_used) < 10)
                
                game.active_puzzle = game.generator.generate_puzzle(
                    force_unused_type=force_unused_type,
                    used_types=game.puzzle_types_used,
                    is_bonus=game.is_bonus_round,
                    num_players=len(game.participants)
                )
                
                game.puzzle_types_used.add(game.active_puzzle['type'])
                if len(game.puzzle_types_used) >= 10:
                    game.puzzle_types_used.clear()
                
                puzzle_desc = game.active_puzzle['description']
                visual_raw = game.active_puzzle.get('visual', '')
                visual = self.format_visual_pattern(visual_raw)
                
                has_pattern = bool(visual)
                is_multi_word = game.active_puzzle.get('multi_word', False)
                round_duration = 20 if has_pattern or is_multi_word else 12
                
                display_text = visual if visual else puzzle_desc
                
                # Consistent embed formatting
                title = f"🎁 BONUS Round {game.round_number} (3x WR!)" if game.is_bonus_round else f"Round {game.round_number}"
                round_embed = discord.Embed(
                    title=title,
                    description=f"{display_text}\n\n\u200b\n\u200b",
                    color=discord.Color.gold() if game.is_bonus_round else discord.Color.green()
                )
                round_embed.set_thumbnail(url=self.signal_urls['green'])
                round_embed.set_footer(text="Type your answer now!" if not is_multi_word else "Type all words you can find!")
                
                msg = await channel.send(embed=round_embed)
                game.game_msg = msg
                game.is_round_active = True
                
                try:
                    if has_pattern or is_multi_word:
                        await asyncio.sleep(8)
                        round_embed.set_thumbnail(url=self.signal_urls['yellow'])
                        round_embed.color = discord.Color.gold()
                        await msg.edit(embed=round_embed)
                        
                        await asyncio.sleep(7)
                        round_embed.set_thumbnail(url=self.signal_urls['red'])
                        round_embed.color = discord.Color.red()
                        await msg.edit(embed=round_embed)
                        
                        await asyncio.sleep(5)
                    else:
                        await asyncio.sleep(5)
                        round_embed.set_thumbnail(url=self.signal_urls['yellow'])
                        round_embed.color = discord.Color.gold()
                        await msg.edit(embed=round_embed)
                        
                        await asyncio.sleep(4)
                        round_embed.set_thumbnail(url=self.signal_urls['red'])
                        round_embed.color = discord.Color.red()
                        await msg.edit(embed=round_embed)
                        
                        await asyncio.sleep(3)
                    
                except asyncio.CancelledError:
                    break
                
                game.is_round_active = False
                
                # Handle multi-word bonus rounds differently
                if is_multi_word:
                    await self.process_multi_word_results(channel, game, msg)
                else:
                    round_embed.set_thumbnail(url=self.signal_urls['unlit'])
                    round_embed.color = discord.Color.dark_gray()
                    
                    if game.winners_in_round:
                        winners_count = len(game.winners_in_round)
                        round_embed.set_footer(text=f"✓ {winners_count} correct guess{'es' if winners_count > 1 else ''}")
                    else:
                        round_embed.set_footer(text="✗ No correct guesses!")
                    
                    await msg.edit(embed=round_embed)
                
                if not game.winners_in_round:
                    game.rounds_without_guess += 1
                else:
                    game.rounds_without_guess = 0
                
                if game.rounds_without_guess >= 5:
                    final_embed = discord.Embed(
                        title="💀 Game Over",
                        description="Five consecutive rounds without correct guesses.\n\n\u200b\n\u200b",
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
                
                await asyncio.sleep(2)

        except Exception as e:
            print(f"Error in Rush Loop: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self.bot.constraint_mode.pop(interaction.channel_id, None)

    async def process_multi_word_results(self, channel, game, msg):
        """Process results for multi-word bonus rounds."""
        if not game.bonus_collected_words:
            round_embed = discord.Embed(
                title=f"🎁 BONUS Round {game.round_number} Results",
                description="No valid words found!\n\n\u200b\n\u200b",
                color=discord.Color.dark_gray()
            )
            round_embed.set_thumbnail(url=self.signal_urls['unlit'])
            await msg.edit(embed=round_embed)
            return
        
        puzzle_type = game.active_puzzle['type']
        
        if puzzle_type == 'longest_word':
            # Find longest word
            winner_id = None
            longest_word = ""
            for uid, words in game.bonus_collected_words.items():
                for word in words:
                    if len(word) > len(longest_word):
                        longest_word = word
                        winner_id = uid
            
            if winner_id:
                game.winners_in_round.append(winner_id)
                wr_gain = 5 * 3  # 3x bonus
                game.add_score(winner_id, wr_gain)
                
                winner_name = await get_cached_username(self.bot, winner_id)
                result_embed = discord.Embed(
                    title=f"🎁 BONUS Round {game.round_number} - Winner!",
                    description=f"🏆 **{winner_name}** wins with **{longest_word.upper()}** ({len(longest_word)} letters)!\n\n+{wr_gain} WR earned\n\n\u200b",
                    color=discord.Color.gold()
                )
                await channel.send(embed=result_embed)
        
        elif puzzle_type == 'most_words':
            # Find who submitted most unique words
            winner_id = None
            max_count = 0
            for uid, words in game.bonus_collected_words.items():
                if len(words) > max_count:
                    max_count = len(words)
                    winner_id = uid
            
            if winner_id:
                game.winners_in_round.append(winner_id)
                wr_gain = 5 * 3  # 3x bonus
                game.add_score(winner_id, wr_gain)
                
                winner_name = await get_cached_username(self.bot, winner_id)
                words_found = game.bonus_collected_words[winner_id]
                result_embed = discord.Embed(
                    title=f"🎁 BONUS Round {game.round_number} - Winner!",
                    description=f"🏆 **{winner_name}** wins with **{max_count} words**!\n\n{', '.join(w.upper() for w in words_found[:5])}{'...' if len(words_found) > 5 else ''}\n\n+{wr_gain} WR earned\n\n\u200b",
                    color=discord.Color.gold()
                )
                await channel.send(embed=result_embed)

    async def show_checkpoint(self, channel, game):
        """Display checkpoint with scores and distribute rewards."""
        checkpoint_embed = discord.Embed(
            title="🏁 Checkpoint",
            description="Calculating scores and distributing rewards...\n\n\u200b\n\u200b\n\u200b",
            color=discord.Color.blue()
        )
        checkpoint_embed.set_thumbnail(url=self.signal_urls['checkpoint'])
        msg = await channel.send(embed=checkpoint_embed)
        
        sorted_scores = sorted(game.scores.items(), key=lambda x: x[1]['wr'], reverse=True)
        
        if not sorted_scores:
            checkpoint_embed.description = "No scores to report this checkpoint.\n\nGet ready, game is about to continue!\n\n\u200b\n\u200b"
            checkpoint_embed.set_thumbnail(url=self.signal_urls['checkpoint'])
            await msg.edit(embed=checkpoint_embed)
            await asyncio.sleep(5)
            return

        lines = []
        medals = ["🥇", "🥈", "🥉"]
        
        # TIER TAX & CONVERSION CONSTANTS
        # Tax: Reduces RP effectiveness based on daily earnings to prevent massive farming
        # But user requested "tier based taxing".
        # Let's simple convert RP -> WR. 1 RP approx 1 WR, but with logic.
        
        for i, (uid, data) in enumerate(sorted_scores):
            user_name = await get_cached_username(self.bot, uid)
            rp_total = data['wr'] # Actually Rush Points
            rounds = data['rounds_won']
            
            # Store Total RP for MVP
            game.total_wr_per_user[uid] = game.total_wr_per_user.get(uid, 0) + rp_total
            
            # --- CONVERSION LOGIC ---
            from src.mechanics.rewards import get_tier_multiplier
            
            # Fetch user profile for Tier Info
            profile = fetch_user_profile_v2(self.bot, uid)
            current_wr = profile.get('multi_wr', 0) if profile else 0
            
            # 1. Base Conversion: 1 RP = 1 WR (Subject to reduction)
            # User said: "subjected to tier based taxing"
            # Higher tier -> LESS return? Or MORE? usually Higher Tier = Harder to climb.
            # Let's assume standard behavior: Higher WR = harder to gain.
            
            modifier = 1.0
            if current_wr > 2000: modifier = 0.8
            if current_wr > 4000: modifier = 0.6
            if current_wr > 6000: modifier = 0.4
            
            final_wr_gain = int(rp_total * modifier)
            if final_wr_gain < 1 and rp_total > 0: final_wr_gain = 1 # Minimum 1 if you played
            
            # XP Calculation
            xp_gain = 35 + max(0, 30 - (i * 5))
            
            try:
                self.bot.supabase_client.rpc('record_game_result_v4', {
                    'p_user_id': uid,
                    'p_guild_id': channel.guild.id if channel.guild else None,
                    'p_mode': 'MULTI',
                    'p_xp_gain': xp_gain,
                    'p_wr_delta': final_wr_gain,
                    'p_is_win': (i == 0),
                    'p_egg_trigger': None
                }).execute()
            except Exception as e:
                print(f"Failed to record checkpoint for {uid}: {e}")
            
            medal = medals[i] if i < 3 else "▫️"
            lines.append(f"{medal} **{user_name}** • {rp_total} pts ({final_wr_gain} WR) • {rounds} rds")

        checkpoint_embed.description = "\n".join(lines) + "\n\nGet ready, game is about to continue!\n\n\u200b"
        checkpoint_embed.color = discord.Color.green()
        checkpoint_embed.set_thumbnail(url=self.signal_urls['checkpoint'])
        await msg.edit(embed=checkpoint_embed)
        
        game.scores = {}
        
        await asyncio.sleep(8)

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        if user.bot:
            return
        cid = reaction.message.channel.id
        if cid not in self.bot.constraint_mode:
            return
        
        game = self.bot.constraint_mode[cid]
        if game.game_msg and reaction.message.id == game.game_msg.id:
            if not game.start_confirmed.is_set():
                game.participants.add(user.id)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return
        cid = message.channel.id
        if cid not in self.bot.constraint_mode:
            return
        
        game = self.bot.constraint_mode[cid]
        if not game.is_round_active or not game.active_puzzle:
            return
        
        content = message.content.strip().lower()
        if not content.isalpha():
            return
        
        # Check if word is in valid dictionary
        puzzle = game.active_puzzle
        is_five_letter_only = puzzle.get('five_letter_only', False)
        
        if is_five_letter_only:
            if len(content) != 5:
                return
            valid_dict = self.bot.valid_set
        else:
            valid_dict = self.bot.valid_set | self.bot.full_dict
        
        if content not in valid_dict:
            return
        
        # For multi-word bonus rounds
        if puzzle.get('multi_word', False):
            if content not in puzzle['solutions']:
                return
            
            if content in game.used_words:
                return
            
            game.used_words.add(content)
            game.participants.add(message.author.id)
            
            if message.author.id not in game.bonus_collected_words:
                game.bonus_collected_words[message.author.id] = []
            game.bonus_collected_words[message.author.id].append(content)
            
            try:
                await message.add_reaction("✅")
            except:
                pass
            return
        
        # Standard rounds
        if content in game.used_words:
            return
        if content not in puzzle['solutions']:
            return
        
        # Check if user already answered this round
        if message.author.id in game.user_answers_this_round:
            try:
                await message.add_reaction("⏭️")  # Already answered
            except:
                pass
            return
        
        game.used_words.add(content)
        game.participants.add(message.author.id)
        game.user_answers_this_round[message.author.id] = content
        
        rank = len(game.winners_in_round) + 1
        game.winners_in_round.append(message.author.id)
        
        # --- RUSH POINTS LOGIC ---
        # 1st: 5 pts, 2nd: 4 pts, 3rd: 3 pts, 4th: 2 pts, Others: 1 pt
        rush_points = 1
        reaction = "✓"
        
        if rank == 1:
            rush_points = 5
            reaction = "🥇"
        elif rank == 2:
            rush_points = 4
            reaction = "🥈"
        elif rank == 3:
            rush_points = 3
            reaction = "🥉"
        elif rank == 4:
            rush_points = 2
            reaction = "⭐"
        
        # Apply bonus multiplier (3x Rush Points)
        if game.is_bonus_round:
            rush_points *= 3
        
        game.add_score(message.author.id, rush_points)
        
        try:
            await message.add_reaction(reaction)
        except:
            pass

async def setup(bot):
    await bot.add_cog(ConstraintMode(bot))
