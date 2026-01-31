import discord
from discord.ext import commands
from discord import ui
import datetime
import asyncio
from src.config import EVENT_ACTIVE, EVENT_WORDS, EVENT_DEADLINE
from src.game import WordleGame
from src.ui import get_markdown_keypad_status
from src.utils import get_badge_emoji, EMOJIS

class ChristmasGuessModal(ui.Modal):
    guess_input = ui.TextInput(label="Enter your 5-letter guess", min_length=5, max_length=5, required=True)

    def __init__(self, bot, game, view_ref, word_index):
        super().__init__(title=f"Christmas Word {word_index + 1}/20")
        self.bot = bot
        self.game = game
        self.view_ref = view_ref
        self.word_index = word_index

    async def on_submit(self, interaction: discord.Interaction):
        if not EVENT_ACTIVE:
            return await interaction.response.send_message("The Christmas Event has ended!", ephemeral=True)

        guess = self.guess_input.value.lower().strip()
        if not guess.isalpha() or len(guess) != 5:
            return await interaction.response.send_message("⚠️ 5 letters only.", ephemeral=True)
        if guess not in self.bot.valid_set:
            return await interaction.response.send_message(f"⚠️ **{guess.upper()}** not in dictionary.", ephemeral=True)
        if self.game.is_duplicate(guess):
             return await interaction.response.send_message(f"⚠️ **{guess.upper()}** already guessed.", ephemeral=True)

        pat, win, game_over = self.game.process_turn(guess, interaction.user)
        
        # Update UI
        await self.view_ref.update_event_message(interaction, win, game_over)

class ChristmasView(ui.View):
    def __init__(self, bot, user_id, progress):
        super().__init__(timeout=None)
        self.bot = bot
        self.user_id = user_id
        self.progress = progress # List of solved word indices
        self.current_word_idx = self._get_next_word_idx()
        self.game = self._start_new_game()
        
    def _get_next_word_idx(self):
        for i in range(len(EVENT_WORDS)):
            if i not in self.progress:
                return i
        return -1

    def _start_new_game(self):
        if self.current_word_idx == -1: return None
        word = EVENT_WORDS[self.current_word_idx]
        game = WordleGame(word, 0, self.user_id, 0)
        game.max_attempts = 7 # Unique 7-try mechanism
        return game

    def _get_countdown(self):
        deadline = datetime.datetime.fromisoformat(EVENT_DEADLINE)
        now = datetime.datetime.now()
        diff = deadline - now
        if diff.total_seconds() <= 0: return "Ended"
        days = diff.days
        hours, rem = divmod(diff.seconds, 3600)
        minutes, _ = divmod(rem, 60)
        return f"{days}d {hours}h {minutes}m"

    async def update_event_message(self, interaction, win=False, game_over=False):
        if self.current_word_idx == -1:
            embed = discord.Embed(title="🎄 Christmas Event Completed! 🎄", color=discord.Color.gold())
            embed.description = "Congratulations! You've solved all 20 words and earned the **Christmas Star** badge! 🌟"
            embed.set_footer(text="Check your /profile to see your new badge.")
            return await interaction.response.edit_message(content="", embed=embed, view=None)

        # 7-try progress bar
        filled = "●" * self.game.attempts_used
        empty = "○" * (7 - self.game.attempts_used)
        progress_bar = f"[{filled}{empty}]"

        board_display = "\n".join([f"{h['pattern']}" for h in self.game.history]) if self.game.history else "No guesses yet."
        keypad = get_markdown_keypad_status(self.game.used_letters, self.bot, self.user_id)
        countdown = self._get_countdown()

        embed = discord.Embed(title=f"🎄 Christmas Event | Word {self.current_word_idx + 1}/20", color=discord.Color.red())
        embed.description = f"**Attempts:** {self.game.attempts_used}/7 {progress_bar}\n**Ends in:** `{countdown}`"
        embed.add_field(name="Board", value=board_display, inline=False)
        
        if win:
            self.progress.append(self.current_word_idx)
            # Save progress to DB
            await self._save_progress()
            
            self.current_word_idx = self._get_next_word_idx()
            if self.current_word_idx == -1:
                # Award badge
                await self._award_badge()
                return await self.update_event_message(interaction)
                
            self.game = self._start_new_game()
            embed.title = "✨ Word Solved! ✨"
            embed.color = discord.Color.green()
            embed.description = f"Great job! Moving to Word {self.current_word_idx + 1}/20.\n**Ends in:** `{countdown}`"
            # Show final board for a moment? No, just progress.
        elif game_over:
            # Failed word -> Get a different word from the pool (Step 3.5)
            # Actually, user said: "If a player fails to find a word, a different word from the 20-word pool is presented"
            # This means we move to the NEXT unsolved word or just reset current if no others.
            # Let's just move to next if possible.
            old_idx = self.current_word_idx
            # Find another unsolved word
            self.current_word_idx = self._get_next_unsolved_excluding(old_idx)
            self.game = self._start_new_game()
            embed.title = "💔 Try Another Word 💔"
            embed.color = discord.Color.orange()
            embed.description = f"You couldn't find the last word. Try Word {self.current_word_idx + 1} instead!\n**Ends in:** `{countdown}`"
        
        message_content = f"**Keyboard Status:**\n{keypad}"
        
        if not interaction.response.is_done():
            await interaction.response.edit_message(content=message_content, embed=embed, view=self)
        else:
            await interaction.edit_original_response(content=message_content, embed=embed, view=self)

    def _get_next_unsolved_excluding(self, idx):
        # Very simple: just find next unsolved. If we are at end, wrap around.
        for i in range(idx + 1, len(EVENT_WORDS)):
            if i not in self.progress: return i
        for i in range(0, idx):
            if i not in self.progress: return i
        return idx # Still unsolved

    async def _save_progress(self):
        # We'll use the 'eggs' field as a proxy or 'event_data' if it exists.
        # Since I can't check schema, I'll try to update 'eggs' or a dedicated field.
        try:
            # We store progress as a comma-separated string in a special egg key
            progress_str = ",".join(map(str, self.progress))
            # Direct update might be tricky if we don't know the full json.
            # We'll use a hack: trigger_egg with special name.
            from src.database import trigger_egg
            # This is a bit of a hack but avoids RPC changes.
            # Actually, I'll just use the bot's supabase client directly to update.
            # We need to be careful not to overwrite other eggs.
            res = self.bot.supabase_client.table('user_stats_v2').select('eggs').eq('user_id', self.user_id).execute()
            eggs = res.data[0]['eggs'] if res.data else {}
            eggs['christmas_event_progress'] = progress_str
            self.bot.supabase_client.table('user_stats_v2').update({'eggs': eggs}).eq('user_id', self.user_id).execute()
        except Exception as e:
            print(f"Error saving event progress: {e}")

    async def _award_badge(self):
        try:
            # Award the actual badge
            res = self.bot.supabase_client.table('user_stats_v2').select('inventory').eq('user_id', self.user_id).execute()
            inv = res.data[0]['inventory'] if res.data else {}
            if not inv: inv = {}
            # Badge list
            badges = inv.get('badges', [])
            if "christmas_star_badge" not in badges:
                badges.append("christmas_star_badge")
                inv['badges'] = badges
                self.bot.supabase_client.table('user_stats_v2').update({'inventory': inv}).eq('user_id', self.user_id).execute()
        except Exception as e:
            print(f"Error awarding event badge: {e}")

    @ui.button(label="Guess", style=discord.ButtonStyle.success, emoji="⌨️")
    async def guess_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(ChristmasGuessModal(self.bot, self.game, self, self.current_word_idx))

    @ui.button(label="Event Info", style=discord.ButtonStyle.secondary, emoji="ℹ️")
    async def info_button(self, interaction: discord.Interaction, button: ui.Button):
        embed = discord.Embed(title="🎄 Christmas Event Info", color=discord.Color.blue())
        embed.description = (
            "Solve all 20 holiday-themed words to earn the exclusive **Christmas Star** badge!\n\n"
            "• **Attempts:** 7 tries per word (Step 3.4)\n"
            "• **Failures:** If you fail a word, you'll be assigned a different one from the pool.\n"
            "• **Progress:** Your progress is saved automatically.\n"
            "• **Deadline:** Event ends on Jan 1, 2026."
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

class ChristmasEvent(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="christmas", description="Open the Christmas Event menu!")
    async def christmas(self, ctx):
        if not EVENT_ACTIVE:
            return await ctx.send("🎄 The Christmas Event is currently inactive. Stay tuned!", ephemeral=True)

        await ctx.defer(ephemeral=True)
        
        # Load progress
        progress = []
        try:
            res = self.bot.supabase_client.table('user_stats_v2').select('eggs').eq('user_id', ctx.author.id).execute()
            if res.data:
                eggs = res.data[0].get('eggs', {})
                prog_str = eggs.get('christmas_event_progress', "")
                if prog_str:
                    progress = [int(x) for x in prog_str.split(",") if x]
        except:
            pass

        view = ChristmasView(self.bot, ctx.author.id, progress)
        
        # Initial Message
        if view.current_word_idx == -1:
            embed = discord.Embed(title="🎄 Christmas Event Completed! 🎄", color=discord.Color.gold())
            embed.description = "You've solved all 20 words! Your **Christmas Star** shines bright. 🌟"
            return await ctx.send(embed=embed, ephemeral=True)

        countdown = view._get_countdown()
        embed = discord.Embed(title=f"🎄 Christmas Event | Word {view.current_word_idx + 1}/20", color=discord.Color.red())
        embed.description = f"Welcome to the holiday event! Solve all words to earn a limited-time badge.\n**Ends in:** `{countdown}`"
        embed.add_field(name="Board", value="No guesses yet.", inline=False)
        
        keypad = get_markdown_keypad_status(view.game.used_letters, self.bot, ctx.author.id)
        message_content = f"**Keyboard Status:**\n{keypad}"

        await ctx.send(content=message_content, embed=embed, view=view, ephemeral=True)

async def setup(bot):
    await bot.add_cog(ChristmasEvent(bot))
