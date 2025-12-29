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
from src.handlers.game_logic import start_multiplayer_game


# ========= CUSTOM MODE MODAL =========
class EnhancedCustomModal(ui.Modal, title="🧂 CUSTOM MODE Setup"):
    word_input = ui.TextInput(
        label="Enter a 5-letter word",
        placeholder="e.g., PIZZA",
        max_length=5,
        min_length=5
    )
    
    tries_input = ui.TextInput(
        label="Number of tries (3-10, default: 6)",
        placeholder="6",
        max_length=2,
        min_length=1,
        required=False,
        default="6"
    )
    
    reveal_input = ui.TextInput(
        label="Reveal word on loss? (yes/no, default: yes)",
        placeholder="yes",
        max_length=3,
        min_length=2,
        required=False,
        default="yes"
    )
    
    keyboard_input = ui.TextInput(
        label="Show keyboard guide? (yes/no, default: yes)",
        placeholder="yes",
        max_length=3,
        min_length=2,
        required=False,
        default="yes"
    )
    
    extra_options = ui.TextInput(
        label="Extra options (optional, see /help)",
        placeholder="dict:apple,grape | time:10 | player:@user | blind:yes | start:crane",
        max_length=300,
        required=False,
        style=discord.TextStyle.paragraph
    )

    def __init__(self, bot, user):
        super().__init__()
        self.bot = bot
        self.user = user

    async def on_submit(self, interaction: discord.Interaction):
        word = self.word_input.value.strip().lower()
        tries_str = self.tries_input.value.strip() or "6"
        reveal = self.reveal_input.value.strip().lower() or "yes"
        keyboard = self.keyboard_input.value.strip().lower() or "yes"
        extra = self.extra_options.value.strip()

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
        
        if keyboard not in ["yes", "no"]:
            return await interaction.response.send_message(
                "❌ Keyboard option must be 'yes' or 'no'.",
                ephemeral=True
            )
        
        # Parse tries
        try:
            tries = int(tries_str)
            if tries < 3 or tries > 10:
                return await interaction.response.send_message(
                    "❌ Number of tries must be between 3 and 10.",
                    ephemeral=True
                )
        except ValueError:
            return await interaction.response.send_message(
                "❌ Invalid number of tries! Must be a number between 3-10.",
                ephemeral=True
            )

        # Parse extra options
        custom_dict = None
        time_limit = None
        allowed_player_id = None
        blind_mode = False
        start_word = None
        
        if extra:
            parts = [p.strip() for p in extra.split('|')]
            for part in parts:
                if part.startswith('dict:'):
                    # Custom dictionary
                    words_str = part[5:].strip()
                    words = [w.strip().lower() for w in words_str.split(',') if w.strip()]
                    # Validate all words are 5 letters
                    if not all(len(w) == 5 and w.isalpha() for w in words):
                        return await interaction.response.send_message(
                            "❌ All custom dictionary words must be exactly 5 letters!",
                            ephemeral=True
                        )
                    custom_dict = set(words)
                    custom_dict.add(word)  # Add secret word to dict
                
                elif part.startswith('time:'):
                    # Time limit in minutes
                    try:
                        time_limit = int(part[5:].strip())
                        if time_limit < 1 or time_limit > 360:
                            return await interaction.response.send_message(
                                "❌ Time limit must be between 1 and 360 minutes!",
                                ephemeral=True
                            )
                    except ValueError:
                        return await interaction.response.send_message(
                            "❌ Invalid time limit! Must be a number.",
                            ephemeral=True
                        )
                
                elif part.startswith('player:'):
                    # Allowed player restriction
                    player_mention = part[7:].strip()
                    # Try to extract user ID from mention or raw ID
                    import re
                    match = re.search(r'<@!?(\d+)>|^(\d+)$', player_mention)
                    if match:
                        allowed_player_id = int(match.group(1) or match.group(2))
                    else:
                        return await interaction.response.send_message(
                            "❌ Invalid player format! Use @mention or user ID.",
                            ephemeral=True
                        )
                
                elif part.startswith('blind:'):
                    # Blind mode
                    val = part[6:].strip().lower()
                    if val in ['yes', 'true', 'on', '1']:
                        blind_mode = True
                
                elif part.startswith('start:'):
                    # Force start word
                    s_word = part[6:].strip().lower()
                    if len(s_word) != 5 or not s_word.isalpha():
                        return await interaction.response.send_message(
                            "❌ Start word must be exactly 5 letters!",
                            ephemeral=True
                        )
                    start_word = s_word

        reveal_bool = reveal == "yes"
        show_keyboard = keyboard == "yes"

        # Check if ANY game already exists in this channel
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
        if custom_dict:
            self.bot.valid_set.update(custom_dict)

        # Create game
        game = WordleGame(word, cid, self.user, 0)
        game.max_attempts = tries
        game.reveal_on_loss = reveal_bool
        game.custom_dict = custom_dict
        game.time_limit = time_limit
        game.allowed_player_id = allowed_player_id
        game.show_keyboard = show_keyboard
        game.blind_mode = blind_mode
        
        # Apply start word if valid
        if start_word:
            # We treat it as a pre-made guess by the system/host
            # Using a dummy user or just appending to history directly
            # For simplicity, we just process it as a turn by the bot (id=bot.user.id if available, else 0)
            # Actually, let's just append to history to avoid validation logic issues
            pat = game.evaluate_guess(start_word)
            game.history.append({'word': start_word, 'pattern': pat, 'user': self.bot.user})
            game.guessed_words.add(start_word)
        self.bot.custom_games[cid] = game

        # Clean up any "stopped" state
        self.bot.stopped_games.discard(cid)

        # Respond to modal
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        # Build setup summary
        setup_details = [
            f"**Tries:** {tries}",
            f"**Reveal on loss:** {'Yes' if reveal_bool else 'No'}",
            f"**Keyboard:** {'Shown' if show_keyboard else 'Hidden'}"
        ]
        if custom_dict:
            setup_details.append(f"**Custom dictionary:** {len(custom_dict)} words")
        if time_limit:
            setup_details.append(f"**Time limit:** {time_limit} minutes")
        if allowed_player_id:
            setup_details.append(f"**Restricted to:** <@{allowed_player_id}>")
        if blind_mode:
            setup_details.append("**Blind Mode:** Active 🙈")
        if start_word:
            setup_details.append(f"**Starting Word:** {start_word.upper()}")

        await interaction.followup.send(
            "✅ Custom game set up!\n" + "\n".join(setup_details),
            ephemeral=True
        )

        # Announce in channel
        embed = discord.Embed(
            title="🧂 Custom Wordle Game Started",
            color=discord.Color.teal()
        )
        desc_parts = [
            f"A custom wordle has been set up by **{self.user.display_name}**",
            f"**{tries} attempts** total"
        ]
        if allowed_player_id:
            desc_parts.append(f"**Restricted to:** <@{allowed_player_id}>")
        if time_limit:
            desc_parts.append(f"**Time limit:** {time_limit} minutes")
        if blind_mode:
            desc_parts.append("**Blind Mode:** Active 🙈")
        
        embed.description = "\n".join(desc_parts)
        embed.add_field(name="How to Play", value="`/guess word:xxxxx` or `-g xxxxx`", inline=False)

        await interaction.channel.send(embed=embed)



# ========= CUSTOM MODE BUTTONS =========
class CustomSetupView(ui.View):
    def __init__(self, bot, user):
        super().__init__(timeout=300)
        self.bot = bot
        self.user = user

    @ui.button(label="Set Up", style=discord.ButtonStyle.primary, emoji="🧂")
    async def setup_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(EnhancedCustomModal(self.bot, self.user))

    @ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer()
        await interaction.delete_original_response()


class GameCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="wordle", description="Start a new game (Simple word list).")
    @commands.guild_only()
    async def start(self, ctx):
        if not ctx.interaction:
            return # Only allow slash command, ignore prefix like -wordle
        await start_multiplayer_game(self.bot, ctx, is_classic=False)

    @commands.hybrid_command(name="wordle_classic", description="Start a Classic game (Harder word list).")
    @commands.guild_only()
    async def start_classic(self, ctx):
        if not ctx.interaction:
            return # Only allow slash command, ignore prefix like -wordle_classic
        await start_multiplayer_game(self.bot, ctx, is_classic=True)

    @commands.hybrid_command(name="solo", description="Play a private game (Ephemeral).")
    async def solo(self, ctx):
        if not ctx.interaction: return
        await ctx.defer(ephemeral=True)
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
        if not ctx.interaction: return
        await ctx.defer(ephemeral=True)
        if ctx.author.id not in self.bot.solo_games:
            return await ctx.send("⚠️ No active solo game to cancel.", ephemeral=True)

        game = self.bot.solo_games.pop(ctx.author.id)
        await ctx.send(f"✅ Solo game cancelled. The word was **{game.secret.upper()}**.", ephemeral=True)

    @commands.hybrid_command(name="stop_game", description="Force stop the current game.")
    @commands.guild_only()
    async def stop_game(self, ctx):
        if not ctx.interaction: return
        await ctx.defer()
        cid = ctx.channel.id
        game = self.bot.games.get(cid)
        custom_game = self.bot.custom_games.get(cid)

        if not game and not custom_game:
            return await ctx.send("No active game to stop.")

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
    @commands.guild_only()
    async def custom_mode(self, ctx):
        if not ctx.interaction: return
        await ctx.defer(ephemeral=True)
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
                  "• A wordle match would start, others can use `/guess` or `-g` to make a guess\n"
                  "• This mode gives **no XP** or **WR** score",
            inline=False
        )
        embed.set_footer(text="You'll be prompted to enter a word and choose if the answer reveals on loss")

        view = CustomSetupView(self.bot, ctx.author)
        await ctx.send(embed=embed, view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(GameCommands(bot))
