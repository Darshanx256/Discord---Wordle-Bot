"""
Game commands cog: /wordle, /wordle_classic, /solo, /show_solo, /cancel_solo, /stop_game, /custom
"""
import asyncio
import discord
from discord.ext import commands
from discord import ui
from src.game import WordleGame
from src.database import get_next_secret, get_next_classic_secret
from src.utils import EMOJIS
from src.ui import SoloView, get_markdown_keypad_status


# ========= CUSTOM MODE MODAL =========
class CustomWordModal(ui.Modal, title="🧂 CUSTOM MODE Setup"):
    word_input = ui.TextInput(label="Enter a 5-letter word", placeholder="e.g., PIZZA", max_length=5, min_length=5)
    reveal_input = ui.TextInput(
        label="Reveal word on loss?", 
        placeholder="yes or no",
        max_length=3,
        min_length=2,
        default="yes"
    )

    def __init__(self, bot, user):
        super().__init__()
        self.bot = bot
        self.user = user

    async def on_submit(self, interaction: discord.Interaction):
        word = self.word_input.value.strip().lower()
        reveal = self.reveal_input.value.strip().lower()

        # Validation
        if not word or not word.isalpha() or len(word) != 5:
            return await interaction.response.send_message(
                "❌ Invalid input! Word must be exactly 5 letters (alphabetic only).",
                ephemeral=True
            )

        if reveal not in ["yes", "no"]:
            return await interaction.response.send_message(
                "❌ Reveal must be 'yes' or 'no'.",
                ephemeral=True
            )

        reveal_bool = reveal == "yes"

        # Check if ANY game already exists in this channel (race condition protection)
        cid = interaction.channel.id
        if cid in self.bot.custom_games:
            return await interaction.response.send_message(
                "⚠️ A custom game is already active in this channel!",
                ephemeral=True
            )
        
        if cid in self.bot.games:
            return await interaction.response.send_message(
                "⚠️ A regular game is already active in this channel!",
                ephemeral=True
            )

        # Add word to valid set temporarily
        self.bot.valid_set.add(word)

        # Create game
        game = WordleGame(word, cid, self.user, 0)
        game.reveal_on_loss = reveal_bool  # Add reveal flag
        self.bot.custom_games[cid] = game

        # Clean up any "stopped" state for this channel so wins are rewarded
        self.bot.stopped_games.discard(cid)

        # Respond to modal
        await interaction.response.send_message(
            "✅ Custom game set up! Game is starting...",
            ephemeral=True
        )

        # Announce in channel
        embed = discord.Embed(
            title="🧂 Custom Wordle Game Started",
            color=discord.Color.teal()
        )
        embed.description = f"A 5-letter custom wordle has been set up by **{self.user.display_name}**\n**6 attempts** total"
        embed.add_field(name="How to Play", value="`/guess word:xxxxx`", inline=False)

        await interaction.channel.send(embed=embed)


# ========= CUSTOM MODE BUTTONS =========
class CustomSetupView(ui.View):
    def __init__(self, bot, user):
        super().__init__(timeout=300)
        self.bot = bot
        self.user = user

    @ui.button(label="Set Up", style=discord.ButtonStyle.primary, emoji="🧂")
    async def setup_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(CustomWordModal(self.bot, self.user))

    @ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer()
        await interaction.delete_original_response()


class GameCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="wordle", description="Start a new game (Simple word list).")
    async def start(self, ctx):
        if not ctx.guild:
            return await ctx.send("❌ Command must be used in a server.", ephemeral=True)
        if not self.bot.secrets:
            return await ctx.send("❌ Simple word list missing.", ephemeral=True)

        cid = ctx.channel.id
        if cid in self.bot.games:
            return await ctx.send("⚠️ Game already active. Use `/stop_game` to end it.", ephemeral=True)
        if cid in self.bot.custom_games:
            return await ctx.send("⚠️ Custom game active. Use `/stop_game` to end it first.", ephemeral=True)

        secret = get_next_secret(self.bot, ctx.guild.id)

        title = "✨ Wordle Started! (Simple)"
        embed = discord.Embed(title=title, color=discord.Color.blue())
        embed.description = f"A simple **5-letter word** has been chosen. **6 attempts** total."
        embed.add_field(name="How to Play", value="`/guess word:xxxxx`", inline=False)

        msg = await ctx.send(embed=embed)

        # Init Game
        self.bot.games[cid] = WordleGame(secret, cid, ctx.author, msg.id)
        self.bot.stopped_games.discard(cid)
        print(f"DEBUG: Game STARTED in Channel {cid}. Active Games: {list(self.bot.games.keys())}")

    @commands.hybrid_command(name="wordle_classic", description="Start a Classic game (Harder word list).")
    async def start_classic(self, ctx):
        if not ctx.guild:
            return await ctx.send("❌ Command must be used in a server.", ephemeral=True)
        if not self.bot.hard_secrets:
            return await ctx.send("❌ Classic word list missing.", ephemeral=True)

        cid = ctx.channel.id
        if cid in self.bot.games:
            return await ctx.send("⚠️ Game already active.", ephemeral=True)
        if cid in self.bot.custom_games:
            return await ctx.send("⚠️ Custom game active. Use `/stop_game` to end it first.", ephemeral=True)

        secret = get_next_classic_secret(self.bot, ctx.guild.id)

        title = "⚔️ Wordle Started! (Classic)"
        embed = discord.Embed(title=title, color=discord.Color.dark_gold())
        embed.description = f"**Hard Mode!** 6 attempts."
        embed.add_field(name="How to Play", value="`/guess word:xxxxx`", inline=False)

        msg = await ctx.send(embed=embed)
        self.bot.games[cid] = WordleGame(secret, cid, ctx.author, msg.id)
        self.bot.stopped_games.discard(cid)
        print(f"DEBUG: Classic Game STARTED in Channel {cid}. Active Games: {list(self.bot.games.keys())}")

    @commands.hybrid_command(name="solo", description="Play a private game (Ephemeral).")
    async def solo(self, ctx):
        if ctx.author.id in self.bot.solo_games:
            return await ctx.send("⚠️ You already have a solo game running!", ephemeral=True)

        secret = __import__('random').choice(self.bot.secrets)
        game = WordleGame(secret, 0, ctx.author, 0)
        self.bot.solo_games[ctx.author.id] = game

        board_display = "No guesses yet."
        keypad = get_markdown_keypad_status(game.used_letters, self.bot, ctx.author.id)
        progress_bar = "[○○○○○○]"

        embed = discord.Embed(title="Solo Wordle | Attempt 0/6", color=discord.Color.gold())
        embed.description = "This game is **private**. Only you can see it.\nUse the button below to guess."
        embed.add_field(name="Board", value=board_display, inline=False)
        embed.set_footer(text=f"6 tries left {progress_bar}")

        message_content = f"**Keyboard Status:**\n{keypad}"

        view = SoloView(self.bot, game, ctx.author)
        await ctx.send(content=message_content, embed=embed, view=view, ephemeral=True)

    @commands.hybrid_command(name="show_solo", description="Show your active solo game (if dismissed).")
    async def show_solo(self, ctx):
        if ctx.author.id not in self.bot.solo_games:
            return await ctx.send("⚠️ No active solo game found.", ephemeral=True)

        game = self.bot.solo_games[ctx.author.id]

        filled = "●" * game.attempts_used
        empty = "○" * (6 - game.attempts_used)
        progress_bar = f"[{filled}{empty}]"

        board_display = "\n".join([f"{h['pattern']}" for h in game.history]) if game.history else "No guesses yet."
        keypad = get_markdown_keypad_status(game.used_letters, self.bot, ctx.author.id)

        embed = discord.Embed(title=f"Solo Wordle | Attempt {game.attempts_used}/6", color=discord.Color.gold())
        embed.add_field(name="Board", value=board_display, inline=False)
        embed.set_footer(text=f"{6 - game.attempts_used} tries left {progress_bar}")

        message_content = f"**Keyboard Status:**\n{keypad}"

        view = SoloView(self.bot, game, ctx.author)
        await ctx.send(content=message_content, embed=embed, view=view, ephemeral=True)

    @commands.hybrid_command(name="cancel_solo", description="Cancel your active solo game.")
    async def cancel_solo(self, ctx):
        if ctx.author.id not in self.bot.solo_games:
            return await ctx.send("⚠️ No active solo game to cancel.", ephemeral=True)

        game = self.bot.solo_games.pop(ctx.author.id)
        await ctx.send(f"✅ Solo game cancelled. The word was **{game.secret.upper()}**.", ephemeral=True)

    @commands.hybrid_command(name="stop_game", description="Force stop the current game.")
    async def stop_game(self, ctx):
        cid = ctx.channel.id
        game = self.bot.games.get(cid)
        custom_game = self.bot.custom_games.get(cid)

        if not game and not custom_game:
            return await ctx.send("No active game to stop.", ephemeral=True)

        # Handle regular game
        if game:
            if (ctx.author.id == game.started_by.id) or ctx.author.guild_permissions.manage_messages:
                self.bot.stopped_games.add(cid)
                self.bot.games.pop(cid)
                await ctx.send(f"🛑 Game stopped. Word: **{game.secret.upper()}**.")

                async def _clear_stopped(ch_id):
                    await asyncio.sleep(300)
                    try:
                        self.bot.stopped_games.discard(ch_id)
                    except:
                        pass

                asyncio.create_task(_clear_stopped(cid))
            else:
                await ctx.send("❌ Only Starter or Admin can stop it.", ephemeral=True)
            return

        # Handle custom game
        if custom_game:
            if (ctx.author.id == custom_game.started_by.id) or ctx.author.guild_permissions.manage_messages:
                self.bot.custom_games.pop(cid)
                await ctx.send(f"🛑 Custom game stopped. Word: **{custom_game.secret.upper()}**.")
            else:
                await ctx.send("❌ Only Starter or Admin can stop it.", ephemeral=True)
            return

    @commands.hybrid_command(name="custom", description="Start a custom Wordle game with your own word.")
    async def custom_mode(self, ctx):
        if not ctx.guild:
            return await ctx.send("❌ Command must be used in a server.", ephemeral=True)

        cid = ctx.channel.id

        # Check if a custom game already exists
        if cid in self.bot.custom_games:
            return await ctx.send("⚠️ A custom game is already active in this channel! Use `/stop_game` to end it.", ephemeral=True)

        # Check if a regular game already exists
        if cid in self.bot.games:
            return await ctx.send("⚠️ A regular game is already active. Use `/stop_game` first.", ephemeral=True)

        embed = discord.Embed(
            title="🧂 CUSTOM MODE",
            color=discord.Color.teal()
        )
        embed.description = "Set up a game in **this** chat with your own custom word"
        embed.add_field(
            name="How it works?",
            value="• Click **Set Up** button below and enter a 5-letter word\n"
                  "• A wordle match would start, others can use `/guess` to make a guess\n"
                  "• This mode gives **no XP** or **WR** score",
            inline=False
        )
        embed.set_footer(text="You'll be prompted to enter a word and choose if the answer reveals on loss")

        view = CustomSetupView(self.bot, ctx.author)
        await ctx.send(embed=embed, view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(GameCommands(bot))
