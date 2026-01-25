import asyncio
import time
import datetime
import random
import discord
from discord.ext import commands
from discord import app_commands
from src.mechanics.constraint_logic import ConstraintGenerator
from src.utils import EMOJIS, get_cached_username, calculate_level
from src.database import fetch_user_profile_v2, get_daily_wr_gain, log_event_v1, update_user_stats_manual
from src.mechanics.rewards import get_tier_multiplier, apply_anti_grind
from src.config import TIERS
#from src.mechanics.streaks import StreakManager

class ConstraintGame:
    def __init__(self, bot, channel_id, started_by, generator, validation_base_5, combined_dict):
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

        self.validation_base_5 = validation_base_5
        self.combined_dict = combined_dict
        self.generator = generator
        
        self.round_task = None
        self.game_msg = None
        self.participants = {started_by.id}
        self.start_confirmed = asyncio.Event()
        self.total_wr_per_user = {}
        self.puzzle_types_used = set()  # Track which puzzle types have been used
        self.rounds_since_last_bonus = 0
        self.is_bonus_round = False
        
        # Stats Tracking
        self.streak_updated_users = set()
        self.fastest_answers = {}  # {uid: min_time_seconds}
        self.local_streaks = {}    # {uid: current_session_streak}
        self.best_local_streaks = {} # {uid: max_session_streak}
        self.round_start_time = 0

    def add_score(self, user_id, wr_gain):
        if user_id not in self.scores:
            self.scores[user_id] = {'wr': 0, 'rounds_won': 0}
        self.scores[user_id]['wr'] += wr_gain
        self.scores[user_id]['rounds_won'] += 1

class RushStartView(discord.ui.View):
    def __init__(self, game):
        super().__init__(timeout=300)
        self.game = game

    async def update_lobby(self, interaction: discord.Interaction):
        embed = interaction.message.embeds[0]
        # Update Participants field
        pts = [f"<@{uid}>" for uid in self.game.participants]
        embed.set_field_at(0, name="Participants", value=", ".join(pts) if pts else "None yet", inline=False)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Join Rush", style=discord.ButtonStyle.primary, emoji="⚡")
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in self.game.participants:
            return await interaction.response.send_message("You're already in the rush!", ephemeral=True)
        
        if len(self.game.participants) >= 10:
            return await interaction.response.send_message("⚠️ The lobby is full! (Max 10 players)", ephemeral=True)

        self.game.participants.add(interaction.user.id)
        await self.update_lobby(interaction)

    @discord.ui.button(label="Start Game", style=discord.ButtonStyle.success, emoji="▶️")
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.game.started_by.id:
            return await interaction.response.send_message("Only the host can start the game.", ephemeral=True)
        
        self.game.start_confirmed.set()
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Dismiss", style=discord.ButtonStyle.danger)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.game.started_by.id and not interaction.user.guild_permissions.manage_messages:
            return await interaction.response.send_message("Only the host or an admin can dismiss.", ephemeral=True)
        
        self.game.is_running = False
        self.game.bot.constraint_mode.pop(self.game.channel_id, None)
        await interaction.response.edit_message(content="🛑 World Rush canceled.", embed=None, view=None)
        self.stop()
    
    async def on_timeout(self):
        """Cleanup if lobby times out."""
        if not self.game.start_confirmed.is_set():
            # If game hasn't started, remove from bot dict
            if self.game.channel_id in self.game.bot.constraint_mode:
                self.game.bot.constraint_mode.pop(self.game.channel_id, None)
            
            # Try to update message
            try:
                if self.game.game_msg:
                    await self.game.game_msg.edit(content="⏰ Rush lobby timed out.", view=None)
            except:
                pass

class ConstraintMode(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.signal_urls = {
            'green': "https://cdn.discordapp.com/emojis/1458452365169528996.png",
            'yellow': "https://cdn.discordapp.com/emojis/1458452285804773490.png",
            'red': "https://cdn.discordapp.com/emojis/1458452196483010691.png",
            'unlit': "https://cdn.discordapp.com/emojis/1458452089494704265.png",
            'checkpoint': "https://cdn.discordapp.com/emojis/1458452466998706196.png",
            'bonus': "https://cdn.discordapp.com/emojis/1458455107631841402.png" 
        }
        
        # Initialize Shared Generator once
        secrets_pool = set(bot.secrets) | set(bot.hard_secrets)
        self.validation_base_5 = secrets_pool | bot.rush_wild_set
        self.combined_dict = self.validation_base_5 | bot.full_dict
        self.generator = ConstraintGenerator(secrets_pool, bot.full_dict, self.combined_dict)

    @app_commands.command(name="word_rush", description="Fast-paced word hunt with linguistic constraints")
    @app_commands.guild_only()
    async def word_rush(self, interaction: discord.Interaction):
        cid = interaction.channel_id
        if cid in self.bot.constraint_mode:
            return await interaction.response.send_message("⚠️ A Word Rush session is already active in this channel.", ephemeral=True)
        
        if cid in self.bot.games or cid in self.bot.custom_games:
             return await interaction.response.send_message("⚠️ A Wordle game is already active here. Finish it first!", ephemeral=True)

        game = ConstraintGame(self.bot, cid, interaction.user, self.generator, self.validation_base_5, self.combined_dict)
        self.bot.constraint_mode[cid] = game
        
        embed = discord.Embed(
            title="⚡ Word Rush",
            description=(
                "**READ RULES BEFORE THE GAME STARTS**\nFind words matching each linguistic constraint!\n"
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
                "• **100 Rounds** of fast-paced action!\n"
                "• **Base Forms Only** (e.g., 'APPLE' ✓, 'APPLES' ✗)\n"
                "• No word reuse in same session\n"
                "• **Rush Points** converted to WR at checkpoints\n"
                "• Game ends after 4 rounds without guesses\n"
                "• Random bonus rounds with 3x Rush Points!\n\n"
                "*New to Rush? Type `/help word_rush` to learn how to score!*"
            ),
            color=discord.Color.from_rgb(88, 101, 242)
        )
        embed.set_thumbnail(url=self.signal_urls['unlit'])
        embed.set_footer(text=f"🎮 Hosted by {interaction.user.display_name}")
        
        # Add initial participant
        pts = [f"<@{uid}>" for uid in game.participants]
        embed.add_field(name="Participants", value=", ".join(pts) if pts else "None yet", inline=False)
        
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
        
        # --- FINAL REWARDS & GAME COUNT ---
        await self.finalize_game_session(game, interaction.channel)
        
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
                    # Use custom emoji from EMOJIS dictionary
                    formatted += EMOJIS.get(f"block_{char_low}_green", char.upper())
                elif char == '-':
                    formatted += EMOJIS.get('unknown', '⬜')
                else:
                    formatted += char
            formatted_lines.append(formatted)
        
        return '\n'.join(formatted_lines)

    async def finalize_game_session(self, game, channel):
        """
        Increments 'games_played' for all participants with >0 WR at the end of the session.
        This ensures we count the game strictly ONCE per session.
        """
        # Distribute any pending rewards from the final partial/checkpoint
        if game.scores:
             await self.distribute_rewards(channel, game)
             game.scores = {}

        if not game.total_wr_per_user:
            return
            
        try:
            # Identify users who actually played/scored
            # User requirement: "atleast some wr (rush points) earned"
            valid_participants = [uid for uid, wr in game.total_wr_per_user.items() if wr > 0]
            
            for uid in valid_participants:
                try:
                    is_victory = (game.round_number >= 100)
                    
                    self.bot.supabase_client.rpc('record_game_result_v4', {
                        'p_user_id': uid,
                        'p_guild_id': channel.guild.id if channel.guild else None,
                        'p_mode': 'MULTI',
                        'p_xp_gain': 0,     # Already awarded
                        'p_wr_delta': 0,    # Already awarded
                        'p_is_win': is_victory,
                        'p_egg_trigger': None
                    }).execute()
                    
                except Exception as e:
                    print(f"Error finalizing stats for {uid}: {e}")
                    
        except Exception as e:
            print(f"Error in finalize_game_session: {e}")

    async def run_game_loop(self, interaction, game):
        try:
            channel = interaction.channel
            
            try:
                # 5 minute timeout matching the lobby view
                await asyncio.wait_for(game.start_confirmed.wait(), timeout=300)
            except asyncio.TimeoutError:
                await channel.send("⏰ Rush cancelled: lobby timed out. (Manual start required)")
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
                
                # Victory at 100 rounds
                if game.round_number > 100:
                    break

                game.winners_in_round = []
                game.user_answers_this_round = {}
                game.bonus_collected_words = {}
                game.rounds_since_last_bonus += 1
                
                # Check if it's time for checkpoint
                if game.round_number > 1 and (game.round_number - 1) % 12 == 0:
                    await self.show_checkpoint(channel, game)
                    if not game.is_running:
                        break
                
                # Guarantee bonus round once before round 20
                if game.round_number == 19 and game.rounds_since_last_bonus >= 18:
                    game.is_bonus_round = True
                else:
                    game.is_bonus_round = (game.rounds_since_last_bonus >= 14 and 
                                          random.random() < 0.25 and 
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
                
                # Constant spacing to prevent morphing
                spacing = "\n\u200b" * 3
                
                spacing = "\n\u200b" * 4 # Extra spacing to lock height
                
                title = f"🎁 BONUS ROUND" if game.is_bonus_round else f"Round {game.round_number}"
                round_embed = discord.Embed(
                    title=title,
                    description=f"# {display_text}{spacing}",
                    color=discord.Color.from_rgb(255, 215, 0) if game.is_bonus_round else discord.Color.from_rgb(46, 204, 113)
                )
                round_embed.set_thumbnail(url=self.signal_urls['green'])
                
                if game.is_bonus_round:
                    round_embed.set_author(name="SPECIAL BONUS: 3x RUSH POINTS", icon_url="https://cdn.discordapp.com/emojis/1321033281982824479.png")
                else:
                    round_embed.set_author(name=f"Word Rush • Round {game.round_number} of 100")
                
                # Rotating footer text
                base_footer = "Type out your guess" if game.round_number % 2 != 0 else "`/stop_rush` to end"
                
                footer_text = f"{base_footer}!" if not is_multi_word else "Type ALL possible words!"
                
                round_embed.set_footer(text=footer_text)
                
                msg = await channel.send(embed=round_embed)
                game.game_msg = msg
                game.is_round_active = True
                game.round_start_time = time.monotonic() # Start stats timer
                
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
                
                # Update Local Streaks for non-winners
                current_winners = set(game.winners_in_round)
                for part_id in game.participants:
                    if part_id not in current_winners:
                        game.local_streaks[part_id] = 0

                if not game.winners_in_round:
                    game.rounds_without_guess += 1
                else:
                    game.rounds_without_guess = 0
                
                # Check Victory Condition first
                if game.round_number >= 100:
                     final_embed = discord.Embed(
                        title="🏆 Rush Victory!",
                        description="You conquered all 100 rounds!\n\n\u200b",
                        color=discord.Color.gold()
                    )
                     if game.total_wr_per_user:
                        sorted_mvp = sorted(game.total_wr_per_user.items(), key=lambda x: x[1], reverse=True)
                        m_id, m_wr = sorted_mvp[0]
                        m_name = await get_cached_username(self.bot, m_id)
                        final_embed.set_thumbnail(url="https://cdn.discordapp.com/emojis/1456199435682975827.png") # Green signal
                        final_embed.add_field(
                            name="👑 Rush Champion",
                            value=f"**{m_name}**\n{m_wr} WR earned",
                            inline=False
                        )
                        # Add Ranks
                        ranks_txt = ""
                        for i, (rid, rpts) in enumerate(sorted_mvp[:5]):
                            rname = await get_cached_username(self.bot, rid)
                            ranks_txt += f"`#{i+1}` **{rname}** - {rpts} pts\n"
                        final_embed.add_field(name="Leaderboard", value=ranks_txt, inline=False)

                     await channel.send(embed=final_embed)
                     await self.finalize_game_session(game, channel)
                     game.is_running = False
                     break

                if game.rounds_without_guess >= 4:
                    final_embed = discord.Embed(
                        title="💀 Game Over",
                        description="Four consecutive rounds without correct guesses.\n\n\u200b\n\u200b",
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
                        
                        # Log Game Completion
                        log_event_v1(
                            bot=self.bot,
                            event_type="word_rush_complete",
                            user_id=m_id,
                            guild_id=channel.guild.id if channel.guild else None,
                            metadata={
                                "round_reached": game.round_number,
                                "mvp_id": m_id,
                                "mvp_points": m_wr,
                                "total_participants": len(game.participants)
                            }
                        )

                    await channel.send(embed=final_embed)
                    await self.finalize_game_session(game, channel)
                    game.is_running = False
                    break
                
                # Clear used words periodically to save memory (every 50 rounds) - DISABLED as per user request
                # if game.round_number % 50 == 0:
                #    game.used_words.clear()
                
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

    async def distribute_rewards(self, channel, game):
        """Distributes rewards for the current accumulated scores in game.scores."""
        if not game.scores:
            return []

        sorted_scores = sorted(game.scores.items(), key=lambda x: x[1]['wr'], reverse=True)
        lines = []
        medals = ["🥇", "🥈", "🥉"]
        
        # OPTIMIZATION: Batch fetch all profiles in ONE DB call
        all_uids = [uid for uid, _ in sorted_scores]
        from src.database import fetch_user_profiles_batched
        profiles_map = fetch_user_profiles_batched(self.bot, all_uids)
        
        # Collect DB updates for background processing
        db_updates = []

        for i, (uid, data) in enumerate(sorted_scores):
            user_name = await get_cached_username(self.bot, uid)
            rp_total = data['wr']
            
            # Store Total RP for MVP if not already accounted for
            # Note: We update total_wr_per_user here for the session MVP tracking
            game.total_wr_per_user[uid] = game.total_wr_per_user.get(uid, 0) + rp_total
            
            # Use batched profile (cached from single query)
            profile = profiles_map.get(uid, {})
            current_wr = profile.get('multi_wr', 0)
            # Use cached daily or estimate 0 (minor loss in accuracy, major gain in speed)
            daily_gain = 0  # Skipping get_daily_wr_gain for speed - already checked at checkpoint anyway
            
            t_mult = get_tier_multiplier(current_wr)
            base_xp = 35 + max(0, 30 - (i * 5))
            base_wr = rp_total
            
            xp_gain = int(base_xp * t_mult)
            wr_gain = int(base_wr * t_mult)
            
            final_xp, final_wr = apply_anti_grind(xp_gain, wr_gain, daily_gain)
            
            if final_wr < 1 and rp_total > 0: final_wr = 1
            if final_xp < 5: final_xp = 5
            
            # Calculate level/tier up messages from simulation
            old_xp = profile.get('xp', 0)
            new_xp = old_xp + final_xp
            old_wr = current_wr
            new_wr = old_wr + final_wr
            
            level_up_msg = ""
            tier_up_msg = ""
            
            if calculate_level(new_xp) > calculate_level(old_xp):
                level_up_msg = f" 🆙 **Lvl {calculate_level(new_xp)}**"
            
            old_tier = None
            new_tier = None
            for t in TIERS:
                if old_tier is None and old_wr >= t['min_wr']: old_tier = t
                if new_tier is None and new_wr >= t['min_wr']: new_tier = t
            
            if new_tier and old_tier and new_tier['min_wr'] > old_tier['min_wr']:
                tier_up_msg = f" 🏆 **{new_tier['name']}!**"
            
            # Queue DB update for background processing
            db_updates.append((uid, final_xp, final_wr))
            
            medal = medals[i] if i < 3 else "▫️"
            lines.append(f"{medal} **{user_name}** • {rp_total} pts (+{final_wr} WR){level_up_msg}{tier_up_msg}")

            # Log Checkpoint Event (fire-and-forget)
            asyncio.create_task(asyncio.to_thread(
                log_event_v1,
                bot=self.bot,
                event_type="word_rush_checkpoint",
                user_id=uid,
                guild_id=channel.guild.id if channel.guild else None,
                metadata={
                    "round_number": game.round_number,
                    "rush_points": rp_total,
                    "wr_gain": final_wr,
                    "rank": i + 1
                }
            ))
        
        # BACKGROUND: Process all DB updates asynchronously
        async def process_db_updates():
            for uid, xp, wr in db_updates:
                try:
                    await asyncio.to_thread(update_user_stats_manual, self.bot, uid, xp, wr, 'MULTI')
                except Exception as e:
                    print(f"Failed to record rewards for {uid}: {e}")
        
        asyncio.create_task(process_db_updates())

        return lines

    async def show_checkpoint(self, channel, game):
        """Display checkpoint with scores and distribute rewards."""
        checkpoint_embed = discord.Embed(
            title="🏁 Checkpoint",
            description="Calculating scores and distributing rewards...\n\n\u200b\n\u200b\n\u200b",
            color=discord.Color.blue()
        )
        checkpoint_embed.set_thumbnail(url=self.signal_urls['checkpoint'])
        msg = await channel.send(embed=checkpoint_embed)
        
        lines = await self.distribute_rewards(channel, game)
        game.scores = {}

        if not lines:
            checkpoint_embed.description = "No scores to report this checkpoint.\n\nGet ready, game is about to continue!\n\n\u200b\n\u200b"
            checkpoint_embed.set_thumbnail(url=self.signal_urls['checkpoint'])
            await msg.edit(embed=checkpoint_embed)
            await asyncio.sleep(5)
            return

        # Basic Stats Summary
        is_solo = len(game.participants) == 1
        stats_text = ""
        
        if is_solo:
            # Personal Best / Streak for Solo
            uid = list(game.participants)[0]
            f_time = game.fastest_answers.get(uid)
            if f_time:
                stats_text += f"⚡ **Personal Best:** {f_time:.2f}s\n"
            
            s_cnt = game.best_local_streaks.get(uid, 0)
            if s_cnt >= 2:
                stats_text += f"🔥 **Best Streak:** {s_cnt} in a row\n"
        else:
            # Rankings / MVP for Multiplayer
            if game.fastest_answers:
                 f_uid, f_time = min(game.fastest_answers.items(), key=lambda x: x[1])
                 f_name = await get_cached_username(self.bot, f_uid)
                 stats_text += f"⚡ **Fastest Reflex:** {f_name} ({f_time:.2f}s)\n"
            
            if game.best_local_streaks:
                 s_uid, s_cnt = max(game.best_local_streaks.items(), key=lambda x: x[1])
                 if s_cnt >= 3:
                     s_name = await get_cached_username(self.bot, s_uid)
                     stats_text += f"🔥 **On Fire:** {s_name} ({s_cnt} in a row!)\n"
        
        if stats_text:
            checkpoint_embed.add_field(name="📊 Quick Stats", value=stats_text, inline=False)

        checkpoint_embed.description = "\n".join(lines) + "\n\nGet ready, game is about to continue!\n\n\u200b"
        checkpoint_embed.color = discord.Color.green()
        checkpoint_embed.set_thumbnail(url=self.signal_urls['checkpoint'])
        await msg.edit(embed=checkpoint_embed)
        
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
                if len(game.participants) < 10:
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
        
        # Only process messages from participants
        if message.author.id not in game.participants:
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
            # Use strict 5-letter base dictionary (excludes guesses_common inflections)
            valid_dict = game.validation_base_5
        else:
            # Use combined pool (base 5s + 6+ letter puzzles)
            valid_dict = game.combined_dict
        
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
            
            # Add reaction with retry for rate limit resilience
            for attempt in range(3):
                try:
                    await message.add_reaction("✅")
                    break
                except discord.HTTPException as e:
                    if e.status == 429:  # Rate limited
                        await asyncio.sleep(0.5 * (attempt + 1))
                    else:
                        break
                except:
                    break
            return
        
        # Standard rounds
        if content in game.used_words:
            return
        
        # Optimize: Use the validator function from the puzzle
        if not puzzle['validator'](content):
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
        
        # --- STATS UPDATE ---
        elapsed = time.monotonic() - game.round_start_time
        if elapsed < game.fastest_answers.get(message.author.id, 9999):
             game.fastest_answers[message.author.id] = elapsed
        
        # Streak
        game.local_streaks[message.author.id] = game.local_streaks.get(message.author.id, 0) + 1
        current_streak = game.local_streaks[message.author.id]
        if current_streak > game.best_local_streaks.get(message.author.id, 0):
            game.best_local_streaks[message.author.id] = current_streak

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
        
        # Add reaction with retry for rate limit resilience
        for attempt in range(3):
            try:
                await message.add_reaction(reaction)
                break
            except discord.HTTPException as e:
                if e.status == 429:  # Rate limited
                    await asyncio.sleep(0.5 * (attempt + 1))
                else:
                    break
            except:
                break

async def setup(bot):
    await bot.add_cog(ConstraintMode(bot))
