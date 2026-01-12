"""
Game commands cog: /wordle, /wordle_classic, /solo, /show_solo, /cancel_solo, /stop_game, /custom
"""
import asyncio
import discord
from discord.ext import commands
from discord import ui
import random
import datetime
import time
from src.game import WordleGame
from src.database import fetch_user_profile_v2
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
        max_length=2000,
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
        custom_dict = set()
        time_limit_mins = None
        allowed_players = set()
        blind_mode = False # False, 'full', 'green'
        start_words = []
        custom_only = False
        custom_title = None
        
        if extra:
            # More robust split to handle cases like spaces around pipes
            parts = [p.strip() for p in extra.split('|') if p.strip()]
            for part in parts:
                if ':' not in part: continue
                key, val = [i.strip() for i in part.split(':', 1)]
                key = key.lower()
                
                if key in ['dict', 'strict_dict']:
                    words = [w.strip().lower() for w in val.split(',') if w.strip()]
                    if not all(len(w) == 5 and w.isalpha() for w in words):
                        return await interaction.response.send_message(
                            "❌ All dictionary words must be exactly 5 letters (alphabetic only)!",
                            ephemeral=True
                        )
                    custom_dict.update(words)
                    if key == 'strict_dict':
                        custom_only = True
                
                elif key == 'time':
                    try:
                        time_val = float(val)
                        if time_val < 0.5 or time_val > 360:
                            return await interaction.response.send_message(
                                "❌ Time limit must be between 0.5 and 360 minutes!",
                                ephemeral=True
                            )
                        time_limit_mins = time_val
                    except ValueError:
                        return await interaction.response.send_message(
                            "❌ Invalid time limit! Must be a number (e.g. 10 or 0.5).",
                            ephemeral=True
                        )
                
                elif key == 'player':
                    entries = [e.strip() for e in val.split(',') if e.strip()]
                    if len(entries) > 20:
                        return await interaction.response.send_message(
                            "❌ Maximum 20 players allowed in a custom game.",
                            ephemeral=True
                        )
                    
                    import re
                    # Security: Only allow members present in this channel
                    channel_members = interaction.channel.members if hasattr(interaction.channel, 'members') else []
                    if not channel_members and interaction.guild:
                        channel_members = interaction.guild.members
                    
                    for entry in entries:
                        found_member = None
                        
                        match = re.search(r'<@!?(\d+)>|^(\d+)$', entry)
                        if match:
                            target_id = int(match.group(1) or match.group(2))
                            # Security: Must be in channel members cache
                            found_member = next((m for m in channel_members if m.id == target_id), None)
                        elif entry.startswith('@'):
                            name_to_find = entry[1:].lower()
                            found_member = next((m for m in channel_members if (m.display_name.lower() == name_to_find or m.name.lower() == name_to_find)), None)
                        
                        if found_member:
                            if found_member.bot:
                                return await interaction.response.send_message(f"❌ `{entry}` is a bot. Only humans can play!", ephemeral=True)
                            allowed_players.add(found_member.id)
                        else:
                            return await interaction.response.send_message(
                                f"❌ Could not find player `{entry}` in this channel. They must be present here to be added.",
                                ephemeral=True
                            )
                
                elif key == 'blind':
                    val_low = val.lower()
                    if val_low in ['yes', 'true', 'on', 'full', '1']:
                        blind_mode = 'full'
                    elif val_low == 'green':
                        blind_mode = 'green'
                
                elif key == 'start':
                    # Support multiple start words
                    words = [w.strip().lower() for w in val.split(',') if w.strip()]
                    if not all(len(w) == 5 and w.isalpha() for w in words):
                        return await interaction.response.send_message(
                            "❌ All starting words must be exactly 5 letters!",
                            ephemeral=True
                        )
                    for sw in words:
                        if sw == word:
                            return await interaction.response.send_message(
                                "❌ A starting word cannot be the answer!",
                                ephemeral=True
                            )
                    if len(words) > 10:
                        return await interaction.response.send_message(
                            "❌ Maximum 10 starting words allowed.",
                            ephemeral=True
                        )
                    start_words = words

                elif key == 'title':
                    # Sanitize title: remove mentions, links, and keep it reasonable length
                    import re
                    # Remove anything that looks like a mention <@...> or <#...> or <@&...>
                    clean_title = re.sub(r'<[@#]&?(\d+)>', '', val).strip()
                    # Character limit
                    if len(clean_title) > 100:
                        clean_title = clean_title[:97] + "..."
                    if clean_title:
                        custom_title = clean_title

        reveal_bool = reveal == "yes"
        show_keyboard = keyboard == "yes"
        
        # Check if we have too many dict words
        if len(custom_dict) > 1000: # Limit to 1000 for robustness
             return await interaction.response.send_message(
                "❌ Custom dictionary is too large! Maximum 1000 words.",
                ephemeral=True
            )

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

        # Add word and dict to valid set temporarily
        self.bot.all_valid_5.add(word)
        if custom_dict:
            self.bot.all_valid_5.update(custom_dict)

        # Create game
        game = WordleGame(word, cid, self.user, 0)
        game.max_attempts = tries
        game.reveal_on_loss = reveal_bool
        game.custom_dict = custom_dict if custom_dict else None
        game.time_limit = time_limit_mins
        game.allowed_players = allowed_players
        game.show_keyboard = show_keyboard
        game.blind_mode = blind_mode
        game.custom_only = custom_only
        game.title = custom_title
        
        # Apply start words
        for sw in start_words:
            pat = game.evaluate_guess(sw)
            game.history.append({'word': sw, 'pattern': pat, 'user': self.bot.user})
            game.guessed_words.add(sw.upper()) # Note: guessed_words uses UPPER in process_turn usually? 
                                             # Actually WordleGame.is_duplicate does word.upper() in self.guessed_words
                                             # So we should add upper.

        if start_words:
            # Add to guessed words set to prevent repeats
            # Note: WordleGame uses upper() for guessed_words set
            for sw in start_words:
                game.guessed_words.add(sw.upper())

        self.bot.custom_games[cid] = game
        self.bot.stopped_games.discard(cid)

        # Launch timer if needed
        if time_limit_mins:
            end_ts = int(time.time() + (time_limit_mins * 60))
            game.monotonic_end_time = time.monotonic() + (end_ts - time.time())
            cog = self.bot.get_cog("GameCommands")
            if cog:
                await cog.start_custom_timer(cid, game)

        # Build setup summary for ephemeral response
        setup_details = [
            f"**Tries:** {tries}",
            f"**Reveal on loss:** {'Yes' if reveal_bool else 'No'}",
            f"**Keyboard:** {'Shown' if show_keyboard else 'Hidden'}"
        ]
        if custom_dict:
            setup_details.append(f"**Custom dictionary:** {len(custom_dict)} words{' (STRICT)' if custom_only else ''}")
        if time_limit_mins:
            if time_limit_mins < 1:
                setup_details.append(f"**Time limit:** {int(time_limit_mins * 60)} seconds")
            else:
                setup_details.append(f"**Time limit:** {time_limit_mins} minutes")
        if allowed_players:
            p_mentions = ", ".join([f"<@{pid}>" for pid in allowed_players])
            setup_details.append(f"**Restricted to:** {p_mentions}")
        if blind_mode:
            blind_tag = "Full 🙈" if blind_mode == 'full' else "Greens Only 🟢"
            setup_details.append(f"**Blind Mode:** {blind_tag}")
        if start_words:
            setup_details.append(f"**Starting Word(s):** {', '.join(w.upper() for w in start_words)}")
        if custom_title:
            setup_details.append(f"**Custom Title:** {custom_title}")

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        await interaction.followup.send(
            "✅ Custom game set up!\n" + "\n".join(setup_details),
            ephemeral=True
        )

        # Announce in channel
        embed_title = f"{custom_title} Started" if custom_title else "🧂 Custom Wordle Game Started"
        embed = discord.Embed(
            title=embed_title,
            color=discord.Color.teal()
        )
        desc_parts = [
            f"A custom wordle has been set up by **{self.user.display_name}**",
            f"**{tries} attempts** total"
        ]
        if allowed_players:
            p_mentions = ", ".join([f"<@{pid}>" for pid in allowed_players])
            desc_parts.append(f"**Restricted to:** {p_mentions}")
        if time_limit_mins:
            t_str = f"{int(time_limit_mins * 60)}s" if time_limit_mins < 1 else f"{time_limit_mins}m"
            desc_parts.append(f"**Time limit:** {t_str}")
        if blind_mode:
            blind_tag = "Active 🙈" if blind_mode == 'full' else "Greens Only 🟢"
            desc_parts.append(f"**Blind Mode:** {blind_tag}")
        
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
        self._custom_timers = {} # channel_id: Task

    def cog_unload(self):
        for task in self._custom_timers.values():
            task.cancel()

    async def _run_custom_timer(self, channel_id, game):
        """Monotonic timer for custom games with a time limit."""
        try:
            while channel_id in self.bot.custom_games:
                now = time.monotonic()
                remaining = game.monotonic_end_time - now
                
                if remaining <= 0:
                    break
                
                if remaining > 60: sleep_time = 30
                elif remaining > 10: sleep_time = 5
                elif remaining > 2: sleep_time = 1
                elif remaining > 0.05: sleep_time = 0.05
                else: sleep_time = 0
                
                if sleep_time > 0:
                    await asyncio.sleep(min(sleep_time, remaining))
                else:
                    break
            
            # Time's up
            if channel_id in self.bot.custom_games:
                game = self.bot.custom_games.pop(channel_id, None)
                if game:
                    channel = self.bot.get_channel(channel_id)
                    if channel:
                        embed = discord.Embed(
                            title="⏰ Time's Up!",
                            color=discord.Color.dark_grey()
                        )
                        desc = "The custom game has timed out."
                        if getattr(game, 'reveal_on_loss', True):
                            desc += f"\nThe word was **{game.secret.upper()}**."
                        embed.description = desc
                        await channel.send(embed=embed)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"❌ Error in custom timer {channel_id}: {e}")
        finally:
            self._custom_timers.pop(channel_id, None)

    async def start_custom_timer(self, channel_id, game):
        """Helper to launch the custom game timer task."""
        if channel_id in self._custom_timers:
            self._custom_timers[channel_id].cancel()
        
        task = asyncio.create_task(self._run_custom_timer(channel_id, game))
        self._custom_timers[channel_id] = task

    @commands.hybrid_command(name="ping", description="Check bot latency.")
    async def ping(self, ctx):
        # WebSocket Ping
        ws_ping = round(self.bot.latency * 1000)
        
        # API Ping (measuring response time)
        start_time = time.monotonic()
        msg = await ctx.send("🏓 Pong...")
        end_time = time.monotonic()
        api_ping = round((end_time - start_time) * 1000)
        
        await msg.edit(content=f"🏓 Pong!\nWebSocket Ping: {ws_ping}ms\nAPI Ping: {api_ping}ms")

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

    @commands.hybrid_command(name="hard_mode", description="Start a new game with OFFICIAL HARD MODE rules.")
    @commands.guild_only()
    async def start_hard(self, ctx):
        if not ctx.interaction: return
        await start_multiplayer_game(self.bot, ctx, is_classic=True, hard_mode=True)

    @commands.hybrid_command(name="solo", description="Play a private game (Ephemeral).")
    async def solo(self, ctx):
        if not ctx.interaction: return
        await ctx.defer(ephemeral=True)
        if ctx.author.id in self.bot.solo_games:
            return await ctx.send("⚠️ You already have a solo game running!", ephemeral=True)

        secret = __import__('random').choice(self.bot.secrets)
        game = WordleGame(secret, 0, ctx.author, 0)
        self.bot.solo_games[ctx.author.id] = game

        progress_bar = f"[{'○' * game.max_attempts}]"

        embed = discord.Embed(title=f"Solo Wordle | Attempt 0/{game.max_attempts}", color=discord.Color.gold())
        embed.description = "This game is **private**. Only you can see it.\nUse the button below to guess."
        embed.set_footer(text=f"{game.max_attempts} tries left {progress_bar}")

        view = SoloView(self.bot, game, ctx.author)
        await ctx.send(embed=embed, view=view, ephemeral=True)

    @commands.hybrid_command(name="show_solo", description="Show your active solo game (if dismissed).")
    async def show_solo(self, ctx):
        if ctx.author.id not in self.bot.solo_games:
            return await ctx.send("⚠️ No active solo game found.", ephemeral=True)

        game = self.bot.solo_games[ctx.author.id]

        filled = "●" * game.attempts_used
        empty = "○" * (6 - game.attempts_used)
        progress_bar = f"[{filled}{empty}]"

        board_display = "\n".join([f"{h['pattern']}" for h in game.history]) if game.history else "No guesses yet."
        keypad = get_markdown_keypad_status(game.used_letters, self.bot, ctx.author.id, blind_mode=getattr(game, 'blind_mode', False))

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
                if getattr(custom_game, 'reveal_on_loss', True):
                    await ctx.send(f"🛑 Custom game stopped. Word: **{custom_game.secret.upper()}**.")
                else:
                    await ctx.send("🛑 Custom game stopped. Word was hidden.")
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
                  "• This mode gives **no XP** or **WR** score\n\n"
                  "*Tip: Use `/help custom` to see all extra options!*",
            inline=False
        )
        embed.set_footer(text="You'll be prompted to enter a word and choose if the answer reveals on loss")

        view = CustomSetupView(self.bot, ctx.author)
        await ctx.send(embed=embed, view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(GameCommands(bot))
