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
        self.scores = {} # user_id: {wr: 0, rounds_won: 0}
        self.rounds_without_guess = 0
        self.active_puzzle = None
        self.used_words = set()
        self.winners_in_round = [] # list of user_ids in order
        self.is_running = True
        self.is_round_active = False
        self.generator = ConstraintGenerator(bot.valid_set)
        self.round_task = None
        self.game_msg = None
        self.participants = {started_by.id} # Track confirmed players
        self.start_confirmed = asyncio.Event()
        self.total_wr_per_user = {} # For final MVP tracking

    def add_score(self, user_id, wr_gain):
        if user_id not in self.scores:
            self.scores[user_id] = {'wr': 0, 'rounds_won': 0}
        self.scores[user_id]['wr'] += wr_gain
        self.scores[user_id]['rounds_won'] += 1

class RushStartView(discord.ui.View):
    def __init__(self, game):
        super().__init__(timeout=60)
        self.game = game

    @discord.ui.button(label="Join Rush", style=discord.ButtonStyle.primary, emoji="🏃")
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.game.participants.add(interaction.user.id)
        await interaction.response.send_message(f"✅ **{interaction.user.display_name}** joined the rush!", ephemeral=True)

    @discord.ui.button(label="START GAME", style=discord.ButtonStyle.success, emoji="▶️")
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.game.started_by.id and not interaction.user.guild_permissions.manage_messages:
            return await interaction.response.send_message("❌ Only the host or an admin can start the game!", ephemeral=True)
        
        self.game.start_confirmed.set()
        await interaction.response.defer()
        self.stop()

class ConstraintMode(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def get_signal_emoji(self, name):
        """Helper to get signal emojis from cache or default."""
        return EMOJIS.get(f"signal_{name}", "🚥")

    @app_commands.command(name="word_rush", description="Fast-paced word hunt with linguistic constraints!")
    @app_commands.guild_only()
    async def word_rush(self, interaction: discord.Interaction):
        """Starts the Constraint Mode (Word Rush) with a beautiful lobby."""
        cid = interaction.channel_id
        if cid in self.bot.constraint_games:
            return await interaction.response.send_message("⚠️ A Word Rush session is already active in this channel!", ephemeral=True)
        
        if cid in self.bot.games or cid in self.bot.custom_games:
             return await interaction.response.send_message("⚠️ A Wordle game is already active here. Finish it first!", ephemeral=True)

        game = ConstraintGame(self.bot, cid, interaction.user)
        self.bot.constraint_games[cid] = game
        
        # LOBBY EMBED (RULES)
        embed = discord.Embed(title="🏃 WORD RUSH LOBBY", color=discord.Color.from_rgb(0, 255, 255))
        embed.set_thumbnail(url="https://i.imgur.com/uW9XyvO.png") # Optional trophy icon or similar
        
        rules = [
            "🎯 **Goal:** Find a 5-letter word matching the constraint.",
            "⏱️ **Speed:** Rounds last only **10 seconds**!",
            "🚥 **Signals:** Green (4s) -> Yellow (3s) -> Red (3s) -> Unlit.",
            "🏁 **Winners:** Top 3 guesses get medal reactions and extra WR!",
            "📉 **Penalty:** Game ends if 3 rounds go without any correct guesses.",
            "🚫 **Reuse:** You cannot use the same word twice in one session."
        ]
        
        embed.description = (
            "### 📜 THE RULES\n" + "\n".join(rules) + 
            "\n\n**Note:** Type directly in chat. Only 5-letter dictionary words count!\n"
            "**Rewards:** WR and XP awarded every **12 rounds** at checkpoints."
        )
        
        embed.add_field(name="🤝 PARTICIPATION", value="Click **Join Rush** or react with ANY emoji to confirm you're playing!", inline=False)
        embed.set_footer(text=f"Hosted by {interaction.user.display_name} • Press START to begin")
        
        view = RushStartView(game)
        await interaction.response.send_message(embed=embed, view=view)
        lobby_msg = await interaction.original_response()
        game.game_msg = lobby_msg
        
        # START GAME LOOP
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
        
        # Show final summary if game had any progress
        if game.total_wr_per_user:
            sorted_mvp = sorted(game.total_wr_per_user.items(), key=lambda x: x[1], reverse=True)
            mvp_id, mvp_wr = sorted_mvp[0]
            mvp_name = await get_cached_username(self.bot, mvp_id)
            
            summary_embed = discord.Embed(title="🏆 RUSH SESSION ENDED", color=discord.Color.gold())
            summary_embed.description = f"### Session MVP: **{mvp_name}** (`{mvp_wr}` WR total)\n\nThanks for playing Word Rush!"
            await interaction.response.send_message(embed=summary_embed)
        else:
            await interaction.response.send_message("🛑 **Word Rush has been stopped!**")
        
        self.bot.constraint_games.pop(cid, None)

    async def run_game_loop(self, interaction, game):
        try:
            channel = interaction.channel
            
            # Wait for manual start or timeout
            try:
                await asyncio.wait_for(game.start_confirmed.wait(), timeout=60)
            except asyncio.TimeoutError:
                if len(game.participants) < 1:
                    await channel.send("🛑 Word Rush cancelled: No participants joined in time.")
                    self.bot.constraint_games.pop(game.channel_id, None)
                    return
            
            # 1. INITIAL GORGEOUS WELCOME
            welcome_msgs = [
                "Sharpen your vocabulary, the rush is starting!",
                "Pattern recognition at its finest. Let's go!",
                "Who has the fastest fingers in the server?",
                "Five letters, one constraint, ten seconds. Good luck!",
                "Word Rush: Where dictionary meets adrenaline."
            ]
            
            welcome_embed = discord.Embed(title="🚦 THE RUSH BEGINS!", color=discord.Color.red())
            
            p_text = f"👥 **{len(game.participants)}** participants confirmed." if len(game.participants) > 1 else "🔦 **You are the only participant.** Good luck!"
            welcome_embed.description = f"### {self.get_signal_emoji('red')}\n{random.choice(welcome_msgs)}\n\n{p_text}\n\nStarting in..."
            
            try:
                await game.game_msg.edit(embed=welcome_embed, view=None)
            except:
                game.game_msg = await channel.send(embed=welcome_embed)

            # Sequence: Red -> Yellow -> Green
            await asyncio.sleep(2)
            welcome_embed.description = f"### {self.get_signal_emoji('yellow')}\nGet ready...\n\n{p_text}"
            welcome_embed.color = discord.Color.gold()
            await game.game_msg.edit(embed=welcome_embed)
            
            await asyncio.sleep(2)
            welcome_embed.description = f"### {self.get_signal_emoji('green')}\n### 🟢 GO GO GO!\n\n{p_text}"
            welcome_embed.color = discord.Color.green()
            await game.game_msg.edit(embed=welcome_embed)
            
            await asyncio.sleep(1)
            try:
                await game.game_msg.delete()
            except: pass

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
                puzzle_desc = game.active_puzzle['description']
                visual = game.active_puzzle['visual']
                
                # Round Start Embed
                round_embed = discord.Embed(title=f"ROUND {game.round_number}", color=discord.Color.green())
                desc = f"# {self.get_signal_emoji('green')}\n\n### {puzzle_desc}"
                if visual:
                    desc += f"\n\n{visual}"
                round_embed.description = desc
                round_embed.set_footer(text="Hurry! Time is ticking... 🟢 -> 🟡 -> 🔴")
                
                msg = await channel.send(embed=round_embed)
                game.game_msg = msg
                game.is_round_active = True
                
                try:
                    # 4s Green
                    await asyncio.sleep(4)
                    round_embed.description = f"# {self.get_signal_emoji('yellow')}\n\n### {puzzle_desc}\n\n**HURRY UP!**"
                    if visual: round_embed.description += f"\n\n{visual}"
                    round_embed.color = discord.Color.gold()
                    await msg.edit(embed=round_embed)
                    
                    # 3s Yellow
                    await asyncio.sleep(3)
                    round_embed.description = f"# {self.get_signal_emoji('red')}\n\n### {puzzle_desc}\n\n**LAST CHANCE!**"
                    if visual: round_embed.description += f"\n\n{visual}"
                    round_embed.color = discord.Color.red()
                    await msg.edit(embed=round_embed)
                    
                    # 3s Red
                    await asyncio.sleep(3)
                except asyncio.CancelledError:
                    break
                
                # End Round
                game.is_round_active = False
                
                # If NO ONE guessed, show some examples
                solutions_text = ""
                if not game.winners_in_round:
                    samples = random.sample(list(game.active_puzzle['solutions']), min(3, len(game.active_puzzle['solutions'])))
                    samples_str = ", ".join([f"`{s.upper()}`" for s in samples])
                    solutions_text = f"\n\n❌ **No one found it!** Examples: {samples_str}"
                
                round_embed.description = f"# {self.get_signal_emoji('unlit')}\nRound Ended!{solutions_text}\n\nUse `/stop_rush` to stop."
                round_embed.color = discord.Color.dark_gray()
                await msg.edit(embed=round_embed)
                
                # Deleting round message to avoid clutter
                await asyncio.sleep(2) # Give users a moment to see examples
                try:
                    await msg.delete()
                except: pass
                
                # Check for game over (no guesses for 3 rounds)
                if not game.winners_in_round:
                    game.rounds_without_guess += 1
                else:
                    game.rounds_without_guess = 0
                
                if game.rounds_without_guess >= 3:
                    final_embed = discord.Embed(title="💀 GAME OVER", color=discord.Color.dark_red())
                    
                    mvp_line = ""
                    if game.total_wr_per_user:
                        sorted_mvp = sorted(game.total_wr_per_user.items(), key=lambda x: x[1], reverse=True)
                        m_id, m_wr = sorted_mvp[0]
                        m_name = await get_cached_username(self.bot, m_id)
                        mvp_line = f"\n\n🏆 **Session MVP:** {m_name} ({m_wr} WR)"

                    final_embed.description = f"### No correct guesses for 3 rounds.{mvp_line}\nThe rush has ended. Better luck next time!"
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
        """Shows scores and persists them to DB."""
        checkpoint_embed = discord.Embed(title="🏁 RUSH CHECKPOINT", color=discord.Color.blue())
        checkpoint_embed.description = f"# {self.get_signal_emoji('unlit')}\n### Finalizing rewards for the last 12 rounds..."
        msg = await channel.send(embed=checkpoint_embed)
        
        # Rankings by total WR accumulated in this checkpoint
        sorted_scores = sorted(game.scores.items(), key=lambda x: x[1]['wr'], reverse=True)
        
        if not sorted_scores:
            checkpoint_embed.description = "### No participants found.\nResetting for the next set of rounds!"
            await msg.edit(embed=checkpoint_embed)
            await asyncio.sleep(5)
            try: await msg.delete()
            except: pass
            return

        # Distribute rewards in DB
        lines = []
        for i, (uid, data) in enumerate(sorted_scores):
            user_name = await get_cached_username(self.bot, uid)
            wr_total = data['wr']
            rounds = data['rounds_won']
            
            # Track for session MVP
            game.total_wr_per_user[uid] = game.total_wr_per_user.get(uid, 0) + wr_total
            
            # Persist to DB
            try:
                # Satisfying reward logic
                xp_gain = 35 + max(0, 30 - (i * 5)) # More satisfying XP
                
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
            
            medal = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else "🏃"
            lines.append(f"{medal} **{user_name}** — +{wr_total} WR | `{rounds}` rounds won")

        checkpoint_embed.add_field(name="🏆 RANKINGS", value="\n".join(lines), inline=False)
        checkpoint_embed.description = f"# {self.get_signal_emoji('green')}\n### Rush continues in 10 seconds!"
        checkpoint_embed.color = discord.Color.green()
        await msg.edit(embed=checkpoint_embed)
        
        # Reset ephemeral scores
        game.scores = {}
        
        await asyncio.sleep(10) 
        try: await msg.delete()
        except: pass

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        """Allow users to confirm participation by reacting to any emoji on the lobby message."""
        if user.bot: return
        cid = reaction.message.channel.id
        if cid not in self.bot.constraint_games: return
        
        game = self.bot.constraint_games[cid]
        if game.game_msg and reaction.message.id == game.game_msg.id:
            if not game.start_confirmed.is_set():
                game.participants.add(user.id)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot: return
        cid = message.channel.id
        if cid not in self.bot.constraint_games: return
        
        game = self.bot.constraint_games[cid]
        if not game.is_round_active or not game.active_puzzle: return
        
        # Check if user is a confirmed participant (if any joined, otherwise anyone can play for first round)
        # But user requested "confirm participation", so let's be strict or lenient.
        # Let's be semi-strict: if they guess correctly, add them to participants if not already.
        
        content = message.content.strip().lower()
        if len(content) != 5 or not content.isalpha(): return
        
        if content in game.used_words: return
        if content not in self.bot.valid_set: return
        if content not in game.active_puzzle['solutions']: return

        # Valid Guess!
        game.used_words.add(content)
        game.participants.add(message.author.id)
        
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
        
        game.add_score(message.author.id, wr_gain)
        
        try:
            await message.add_reaction(reaction)
        except: pass

async def setup(bot):
    await bot.add_cog(ConstraintMode(bot))
