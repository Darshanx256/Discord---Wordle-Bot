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
        self.round_guesses = {}  # Track guesses per user per round
        self.is_running = True
        self.is_round_active = False
        self.is_bonus_round = False
        self.bonus_word_count = {}  # For bonus rounds: user_id -> word count
        # Use full NLTK dictionary for variety
        self.generator = ConstraintGenerator()  # No param = full dict
        self.dictionary = set(self.generator.dictionary)  # For validation
        self.round_task = None
        self.game_msg = None
        self.participants = {started_by.id}
        self.start_confirmed = asyncio.Event()
        self.total_wr_per_user = {}
        self.next_bonus_round = random.randint(8, 12)  # First bonus between 8-12

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
        # Ensure NLTK is loaded (handled in constraint_logic)
        self.signal_urls = {
            'green': "https://cdn.discordapp.com/emojis/1456199435682975827.png",
            'yellow': "https://cdn.discordapp.com/emojis/1456199439277494418.png",
            'red': "https://cdn.discordapp.com/emojis/1456199431803244624.png",
            'unlit': "https://cdn.discordapp.com/emojis/1456199350693789696.png",
            'checkpoint': "https://cdn.discordapp.com/emojis/1456313204597588101.png",
            'unknown': "https://cdn.discordapp.com/emojis/EMOJI_ID_HERE.png"
        }

    @app_commands.command(name="word_rush", description="Fast-paced word hunt with linguistic constraints")
    @app_commands.guild_only()
    async def word_rush(self, interaction: discord.Interaction):
        cid = interaction.channel_id
        if cid in self.bot.constraint_games:
            return await interaction.response.send_message("⚠️ A Word Rush session is already active in this channel.", ephemeral=True)
        
        if cid in self.bot.games or cid in self.bot.custom_games:
             return await interaction.response.send_message("⚠️ A Wordle game is already active here. Finish it first!", ephemeral=True)

        # Defer response immediately to prevent interaction timeout
        await interaction.response.defer()
        
        # Initialize game (this may take time due to dictionary loading)
        game = ConstraintGame(self.bot, cid, interaction.user)
        self.bot.constraint_games[cid] = game
        
        embed = discord.Embed(
            title="⚡ Word Rush",
            description=(
                "Hunt words (4-8 letters) matching wild constraints!\n"
                "Rounds: **12-20s** with traffic lights.\n\n"
                "**🎯 Scoring**\n"
                "```\n"
                "1st → 5 WR | 2nd → 4 WR | 3rd → 3 WR\n"
                "4th → 2 WR | Others → 1 WR\n"
                "```\n"
                "**🎁 Bonuses**\n"
                "Random **2x WR** twists + special modes!\n\n"
                "**📋 Rules**\n"
                "• No repeats per session\n"
                "• 1 guess/person/round (except bonuses)\n"
                "• Rewards every 12 rounds\n"
                "• Ends after 5 blank rounds\n\n"
                "Vocabulary blitz awaits—join up!"
            ),
            color=discord.Color.from_rgb(88, 101, 242)
        )
        embed.set_thumbnail(url=self.signal_urls['unlit'])
        embed.set_footer(text=f"🎮 Hosted by {interaction.user.display_name}")
        
        view = RushStartView(game)
        lobby_msg = await interaction.followup.send(embed=embed, view=view)
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

    def format_visual_pattern(self, visual, expected_length):
        """Convert text pattern to emoji blocks, padded for consistency."""
        if not visual:
            return ""
        
        # Pad to max expected length for visual consistency (e.g., 8 chars)
        visual = visual.ljust(expected_length, '-')
        lines = visual.split('\n')
        formatted_lines = []
        
        for line in lines:
            formatted = ""
            for char in line[:8]:  # Cap at 8 for embed row consistency
                char_low = char.lower()
                if char_low.isalpha():
                    block = EMOJIS.get(f"block_{char_low}_green", "")
                    if block:
                        formatted += block
                elif char == '-':
                    formatted += EMOJIS.get("unknown", "⬜")
                else:
                    formatted += char
            formatted_lines.append(formatted)
        
        return '\n'.join(formatted_lines)[:1024]  # Embed limit safety

    def generate_bonus_puzzle(self, game):
        """Generate special bonus round puzzles with variety."""
        bonus_types = [
            self._bonus_most_words,
            self._bonus_speed_demon,
            self._bonus_alphabet_soup,
            self._bonus_rhyme_time,
            self._bonus_jumble_frenzy  # New bonus: multiple jumbles
        ]
        
        return random.choice(bonus_types)(game)
    
    def _bonus_most_words(self, game):
        """Find as many words as possible matching the constraint."""
        word = random.choice(list(game.dictionary))
        letter = random.choice([c for c in set(word)])
        
        solutions = [w for w in game.dictionary if letter in w and w not in game.used_words]
        
        return {
            'description': f"🎁 **BONUS: Most Words**\nMax words with **{letter.upper()}**!",
            'solutions': set(solutions),
            'visual': None,
            'type': 'most_words',
            'expected_length': None  # Variable
        }
    
    def _bonus_speed_demon(self, game):
        """Simple constraint, first gets huge bonus."""
        word = random.choice(list(game.dictionary))
        sub = word[1:3]
        solutions = [w for w in game.dictionary if sub in w and w not in game.used_words]
        
        return {
            'description': f"🎁 **BONUS: Speed**\n1st gets **3x WR**! Contains **{sub.upper()}**",
            'solutions': set(solutions),
            'visual': None,
            'type': 'speed_demon',
            'expected_length': len(word)
        }
    
    def _bonus_alphabet_soup(self, game):
        """Words with alphabetical sequences."""
        def has_alphabetical_sequence(w):
            for i in range(len(w) - 2):
                if ord(w[i]) < ord(w[i+1]) < ord(w[i+2]):
                    return True
            return False
        
        solutions = [w for w in game.dictionary if has_alphabetical_sequence(w) and w not in game.used_words]
        
        return {
            'description': f"🎁 **BONUS: Alphabet**\n3+ letters in order! (e.g., FIRST: RST)",
            'solutions': set(solutions),
            'visual': None,
            'type': 'most_words',
            'expected_length': None
        }
    
    def _bonus_rhyme_time(self, game):
        """Rhyming frenzy."""
        word = random.choice(list(game.dictionary))
        rhyme_len = random.choice([2, 3])
        ending = word[-rhyme_len:]
        
        solutions = [w for w in game.dictionary if w.endswith(ending) and w not in game.used_words]
        
        return {
            'description': f"🎁 **BONUS: Rhyme**\nWords ending **-{ending.upper()}**!",
            'solutions': set(solutions),
            'visual': None,
            'type': 'most_words',
            'expected_length': None
        }
    
    def _bonus_jumble_frenzy(self, game):
        """Bonus: Unscramble a tough jumble, multiple tries."""
        word = random.choice(list(game.dictionary))
        scrambled = ''.join(random.sample(word, len(word)))
        
        solutions = {word}  # Single for frenzy, but allow multiple guesses? Wait, keep as is.
        
        return {
            'description': f"🎁 **BONUS: Jumble**\nUnscramble: **{scrambled.upper()}** (tough one!)",
            'solutions': set(solutions),
            'visual': scrambled.upper(),
            'type': 'jumble',
            'expected_length': len(word)
        }

    async def run_game_loop(self, interaction, game):
        try:
            channel = interaction.channel
            
            try:
                await asyncio.wait_for(game.start_confirmed.wait(), timeout=60)
            except asyncio.TimeoutError:
                if len(game.participants) < 1:
                    await channel.send("⏰ Rush cancelled: no participants joined in time.")
                    self.bot.constraint_games.pop(game.channel_id, None)
                    return
            
            # Countdown sequence
            countdown_embed = discord.Embed(
                title="Starting Rush",
                description="READY?",
                color=discord.Color.from_rgb(220, 20, 60)
            )
            countdown_embed.set_thumbnail(url=self.signal_urls['red'])
            
            try:
                await game.game_msg.edit(embed=countdown_embed, view=None)
            except:
                game.game_msg = await channel.send(embed=countdown_embed)

            await asyncio.sleep(1.2)
            countdown_embed.set_thumbnail(url=self.signal_urls['yellow'])
            countdown_embed.description = "GET SET!"
            countdown_embed.color = discord.Color.gold()
            await game.game_msg.edit(embed=countdown_embed)
            
            await asyncio.sleep(1.2)
            countdown_embed.set_thumbnail(url=self.signal_urls['green'])
            countdown_embed.description = "GO!"
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
                game.round_guesses = {}
                game.is_bonus_round = False
                game.bonus_word_count = {}
                
                if game.round_number > 1 and (game.round_number - 1) % 12 == 0:
                    await self.show_checkpoint(channel, game)
                    if not game.is_running:
                        break

                # Check for bonus round
                if game.round_number == game.next_bonus_round:
                    game.is_bonus_round = True
                    game.next_bonus_round = game.round_number + random.randint(25, 35)
                    
                    # Bonus announcement
                    bonus_announce = discord.Embed(
                        title="🎁 BONUS ROUND!",
                        description="**Double WR** + special twist!\nGet ready...",
                        color=discord.Color.gold()
                    )
                    bonus_msg = await channel.send(embed=bonus_announce)
                    await asyncio.sleep(3)
                    try:
                        await bonus_msg.delete()
                    except:
                        pass

                # Generate puzzle
                if game.is_bonus_round:
                    game.active_puzzle = self.generate_bonus_puzzle(game)
                else:
                    game.active_puzzle = game.generator.generate_puzzle()
                
                puzzle_desc = game.active_puzzle['description']
                visual_raw = game.active_puzzle.get('visual', '')
                expected_length = game.active_puzzle.get('expected_length', 5)
                visual = self.format_visual_pattern(visual_raw, expected_length)
                
                has_pattern = bool(visual)
                puzzle_type = game.active_puzzle.get('type', 'normal')
                
                # Determine round duration based on complexity
                if game.is_bonus_round:
                    round_duration = 20
                elif has_pattern or puzzle_type in ['jumble', 'rhyme']:
                    round_duration = 18
                else:
                    round_duration = 12
                
                # Consistent display: desc or visual, with length hint if needed
                display_text = f"{visual}\n*(Len: {expected_length})*" if visual else f"{puzzle_desc}\n*(Len: {expected_length})*"
                
                round_embed = discord.Embed(
                    title=f"{'🎁 BONUS ' if game.is_bonus_round else ''}Round {game.round_number}",
                    description=display_text,
                    color=discord.Color.gold() if game.is_bonus_round else discord.Color.green()
                )
                round_embed.set_thumbnail(url=self.signal_urls['green'])
                round_embed.set_footer(text="Type your answer!" + (" • 2x WR!" if game.is_bonus_round else ""))
                
                msg = await channel.send(embed=round_embed)
                game.game_msg = msg
                game.is_round_active = True
                
                try:
                    if round_duration >= 18:
                        await asyncio.sleep(8)
                        round_embed.set_thumbnail(url=self.signal_urls['yellow'])
                        round_embed.color = discord.Color.gold()
                        await msg.edit(embed=round_embed)
                        
                        await asyncio.sleep(7)
                        round_embed.set_thumbnail(url=self.signal_urls['red'])
                        round_embed.color = discord.Color.red()
                        await msg.edit(embed=round_embed)
                        
                        await asyncio.sleep(round_duration - 15)
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
                
                round_embed.set_thumbnail(url=self.signal_urls['unlit'])
                round_embed.color = discord.Color.dark_gray()
                
                # Handle bonus round scoring
                if game.is_bonus_round and puzzle_type == 'most_words':
                    if game.bonus_word_count:
                        sorted_players = sorted(game.bonus_word_count.items(), key=lambda x: x[1], reverse=True)
                        winner_id, winner_count = sorted_players[0]
                        
                        # Award bonus points
                        for i, (uid, count) in enumerate(sorted_players):
                            if i == 0:
                                wr = count * 10
                            elif i == 1:
                                wr = count * 7
                            elif i == 2:
                                wr = count * 5
                            else:
                                wr = count * 3
                            
                            game.add_score(uid, wr)
                        
                        round_embed.set_footer(text=f"🏆 Most: {await get_cached_username(self.bot, winner_id)} ({winner_count})")
                    else:
                        round_embed.set_footer(text="✗ No guesses!")
                
                elif game.winners_in_round:
                    winners_count = len(game.winners_in_round)
                    round_embed.set_footer(text=f"✓ {winners_count} correct{'es' if winners_count > 1 else ''}" + (" • 2x!" if game.is_bonus_round else ""))
                else:
                    round_embed.set_footer(text="✗ No correct!")
                
                await msg.edit(embed=round_embed)
                
                if not game.winners_in_round and not game.bonus_word_count:
                    game.rounds_without_guess += 1
                else:
                    game.rounds_without_guess = 0
                
                if game.rounds_without_guess >= 5:
                    final_embed = discord.Embed(
                        title="💀 Game Over",
                        description="Five blank rounds in a row.",
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
        finally:
            self.bot.constraint_games.pop(interaction.channel_id, None)

    async def show_checkpoint(self, channel, game):
        """Display checkpoint with scores and distribute rewards."""
        checkpoint_embed = discord.Embed(
            title="Checkpoint",
            description="Calculating scores and distributing rewards...",
            color=discord.Color.blue()
        )
        checkpoint_embed.set_thumbnail(url=self.signal_urls['checkpoint'])
        msg = await channel.send(embed=checkpoint_embed)
        
        sorted_scores = sorted(game.scores.items(), key=lambda x: x[1]['wr'], reverse=True)
        
        if not sorted_scores:
            checkpoint_embed.description = "No scores to report.\n\nContinuing in 5 seconds..."
            checkpoint_embed.set_thumbnail(url=self.signal_urls['checkpoint'])
            await msg.edit(embed=checkpoint_embed)
            await asyncio.sleep(5)
            return

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
        checkpoint_embed.set_thumbnail(url=self.signal_urls['checkpoint'])
        await msg.edit(embed=checkpoint_embed)
        
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
        expected_length = game.active_puzzle.get('expected_length', 5)
        if len(content) != expected_length or not content.isalpha():
            return
        
        if content in game.used_words:
            return
        if content not in game.dictionary:
            return
        if content not in game.active_puzzle['solutions']:
            return

        # Check if user already guessed this round (except bonus most_words)
        puzzle_type = game.active_puzzle.get('type', 'normal')
        if puzzle_type != 'most_words' and message.author.id in game.round_guesses:
            return

        # Valid guess
        game.used_words.add(content)
        game.participants.add(message.author.id)
        game.round_guesses[message.author.id] = content
        
        # Handle bonus rounds
        if game.is_bonus_round:
            puzzle_type = game.active_puzzle.get('type', 'normal')
            
            if puzzle_type == 'most_words':
                # Count words for this user
                game.bonus_word_count[message.author.id] = game.bonus_word_count.get(message.author.id, 0) + 1
                try:
                    await message.add_reaction("✅")
                except:
                    pass
                return
            
            elif puzzle_type == 'speed_demon':
                # First person gets 3x, others 2x
                rank = len(game.winners_in_round) + 1
                game.winners_in_round.append(message.author.id)
                
                if rank == 1:
                    wr_gain = 15  # 5 * 3
                    reaction = "🥇"
                elif rank == 2:
                    wr_gain = 8  # 4 * 2
                    reaction = "🥈"
                elif rank == 3:
                    wr_gain = 6  # 3 * 2
                    reaction = "🥉"
                else:
                    wr_gain = 2  # 1 * 2
                    reaction = "⭐"
                
                game.add_score(message.author.id, wr_gain)
                
                try:
                    await message.add_reaction(reaction)
                except:
                    pass
                return
        
        # Normal round scoring
        rank = len(game.winners_in_round) + 1
        game.winners_in_round.append(message.author.id)
        
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
        
        # Apply 2x multiplier for non-speed-demon bonus rounds
        if game.is_bonus_round:
            wr_gain *= 2
        
        game.add_score(message.author.id, wr_gain)
        
        try:
            await message.add_reaction(reaction)
        except:
            pass

async def setup(bot):
    await bot.add_cog(ConstraintMode(bot))
