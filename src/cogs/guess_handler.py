"""
Guess handler cog: /guess command with win/loss logic
Enhanced UI: Board + Keyboard in embed description
"""
import asyncio
import datetime
import random
import traceback
import discord
from discord.ext import commands
from discord import app_commands
from src.utils import (
    get_badge_emoji,
    get_cached_username,
    EMOJIS,
    EGG_COOLDOWN_SECONDS,
    roll_easter_egg,
    format_egg_message,
    format_attempt_footer,
)
from src.ui import get_markdown_keypad_status
from src.handlers.game_logic import handle_game_win, handle_game_loss, PlayAgainView
from src.database import trigger_egg


class _InteractionGuessContext:
    """Small adapter so modal/app-command guesses can reuse Context-based flow."""
    def __init__(self, interaction: discord.Interaction):
        self.interaction = interaction
        self.guild = interaction.guild
        self.channel = interaction.channel
        self.author = interaction.user

    async def defer(self):
        if not self.interaction.response.is_done():
            await self.interaction.response.defer()

    async def send(self, content=None, *, embed=None, view=None, ephemeral=False):
        kwargs = {"ephemeral": ephemeral}
        if content is not None:
            kwargs["content"] = content
        if embed is not None:
            kwargs["embed"] = embed
        if view is not None:
            kwargs["view"] = view

        if not self.interaction.response.is_done():
            await self.interaction.response.send_message(**kwargs)
        else:
            await self.interaction.followup.send(**kwargs)


class GuessHandler(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _build_game_embed(self, title: str | None, color, board: str, keypad: str, footer: str,
                          header_text: str = "", show_keyboard: bool = True) -> discord.Embed:
        """
        Build a unified game embed with board and keyboard in description.
        4096 char limit for description should handle 10 tries + keyboard easily.
        """
        embed = discord.Embed(color=color)
        if title:
            embed.title = title
        
        # Build description with visual separators
        desc_parts = []
        if header_text:
            desc_parts.append(header_text)
        
        desc_parts.append(board)
        
        if show_keyboard and keypad:
            desc_parts.append(keypad)
        
        embed.description = "\n\n".join(desc_parts)
        embed.set_footer(text=footer)
        return embed

    #async def _delayed_ephemeral_streak(self, ctx, user, message, delay=2):
    #    """Helper to send delayed, private streak/badge updates (DMs prioritized)."""
    #    from src.utils import send_smart_message
    #    await asyncio.sleep(delay)
    #    await send_smart_message(ctx, message, ephemeral=True, transient_duration=15, user=user)

    async def _handle_guess_ctx(self, ctx, word: str):
        await ctx.defer()
        if not ctx.guild:
            return await ctx.send("Guild only.", ephemeral=True)

        cid = ctx.channel.id
        
        try:
            game = self.bot.games.get(cid)
            custom_game = self.bot.custom_games.get(cid)
            g_word = word.strip().lower()

            # Word Rush route: allows slash/modal guesses when message-content intent is disabled.
            rush_cog = self.bot.get_cog("ConstraintMode")
            if cid in self.bot.constraint_mode and rush_cog:
                handled = await rush_cog.process_rush_guess(
                    channel=ctx.channel,
                    author=ctx.author,
                    content=g_word,
                    interaction=getattr(ctx, "interaction", None),
                )
                if handled:
                    return

            # Check which game type is active
            is_custom = False
            if custom_game:
                game = custom_game
                is_custom = True
            elif not game:
                return await ctx.send("⚠️ No active game.", ephemeral=True)

            # Secret selection is async for multiplayer startup; block guesses until resolved.
            if not is_custom and (not getattr(game, "secret", None) or str(game.secret).upper() == "LOADING"):
                return await ctx.send("Youre too fast, try again!", ephemeral=True)
            
            # Custom game validations
            if is_custom:
                if game.allowed_players and ctx.author.id not in game.allowed_players:
                    return await ctx.send("❌ This game is restricted to a specific player!", ephemeral=True)
                if game.allowed_players and not getattr(game, "player_lock_confirmed", True):
                    return await ctx.send("⏳ Waiting for all locked players to press `Ready`.", ephemeral=True)
                
                if game.custom_dict and g_word not in game.custom_dict:
                    # If it's custom_only, we fail here. If not, we fall through to bot.valid_set check.
                    if getattr(game, 'custom_only', False):
                        return await ctx.send(f"⚠️ **{g_word.upper()}** not in custom dictionary! Only custom words allowed.", ephemeral=True)

            if game.is_duplicate(g_word):
                return await ctx.send(f"⚠️ **{g_word.upper()}** already guessed!", ephemeral=True)
            if len(g_word) != 5 or not g_word.isalpha():
                return await ctx.send("⚠️ 5 letters only.", ephemeral=True)
            
            # Determine valid word set based on custom_only setting
            if is_custom and getattr(game, 'custom_only', False):
                valid_check = game.custom_dict or set()
                if game.secret.lower() not in valid_check:
                    valid_check.add(game.secret.lower())
            elif is_custom and game.custom_dict:
                valid_check = self.bot.all_valid_5 | game.custom_dict
            else:
                valid_check = self.bot.all_valid_5
            
            if g_word not in valid_check:
                return await ctx.send(f"⚠️ **{g_word.upper()}** not in dictionary.", ephemeral=True)

            # Hard Mode Validation
            if getattr(game, 'hard_mode', False):
                is_valid, err_msg = game.validate_hard_mode_guess(g_word)
                if not is_valid:
                    return await ctx.send(f"🚫 {err_msg}", ephemeral=True)

            pat, win, game_over = game.process_turn(g_word, ctx.author)

            # Easter Egg trigger (rate-limited, uniform)
            try:
                now_ts = datetime.datetime.now().timestamp()
                last = self.bot.egg_cooldowns.get(ctx.author.id, 0)
                if now_ts - last >= EGG_COOLDOWN_SECONDS:
                    self.bot.egg_cooldowns[ctx.author.id] = now_ts
                    is_classic = game.secret in getattr(self.bot, 'hard_secrets', [])
                    egg = roll_easter_egg(is_classic)
                    if egg:
                        try:
                            task = asyncio.create_task(asyncio.to_thread(trigger_egg, self.bot, ctx.author.id, egg))
                            task.add_done_callback(self.bot._handle_task_exception)
                        except (RuntimeError, TypeError) as e:
                            print(f"⚠️ Failed to trigger egg task: {e}")
                        try:
                            msg = format_egg_message(egg, ctx.author.display_name, EMOJIS)
                            await ctx.channel.send(msg)
                        except (discord.HTTPException, AttributeError) as e:
                            pass
            except Exception as e:
                print(f"⚠️ Easter egg trigger failed: {e}")

            # Get keyboard status
            key_blind = game.blind_mode if not (win or game_over) else False
            keypad = get_markdown_keypad_status(game.used_letters, self.bot, ctx.author.id, blind_mode=key_blind)
            
            # Progress bar
            filled = "●" * game.attempts_used
            empty = "○" * (game.max_attempts - game.attempts_used)
            progress = f"[{filled}{empty}]"
            
            # Board display
            if is_custom and game.blind_mode and not (win or game_over):
                lines = []
                for h in game.history:
                    masked = ""
                    guess_word = h['word'].upper()
                    for i, char in enumerate(guess_word):
                        char_low = char.lower()
                        # 'full' blind mode hides EVERYTHING with black blocks
                        if game.blind_mode == 'full':
                            masked += EMOJIS.get("block_black", "⬛")
                        # 'green' blind mode shows greens, but others appear as 'absent' grey letters
                        elif game.blind_mode == 'green':
                            if char == game.secret[i].upper():
                                masked += EMOJIS.get(f"block_{char_low}_green", "🟩")
                            else:
                                masked += EMOJIS.get(f"block_{char_low}_absent", "⬜")
                    lines.append(masked)
                board_display = "\n".join([f"{line}" for line in lines])
            else:
                board_display = "\n".join([f"{h['pattern']}" for h in game.history])

            # Fetch player badge (use cached profile - avoids DB hit on every guess)
            active_badge = None
            try:
                from src.database import fetch_user_profile_v2
                cached_profile = fetch_user_profile_v2(self.bot, ctx.author.id, use_cache=True)
                if cached_profile:
                    active_badge = cached_profile.get('active_badge')
            except (KeyError, TypeError, AttributeError) as e:
                pass
            badge_str = f" {get_badge_emoji(active_badge)}" if active_badge else ""

            # Determine if keyboard should be shown
            show_kb = (not is_custom) or game.show_keyboard

            if is_custom:
                # ========= CUSTOM GAME =========
                if win:
                    board_display = "\n".join([f"{h['pattern']}" for h in game.history])
                    embed = self._build_game_embed(
                        title="🏆 VICTORY!",
                        color=discord.Color.green(),
                        board=board_display,
                        keypad=keypad,
                        footer=f"Attempts: {filled}{empty} | Custom mode (no rewards)",
                        header_text=f"**{ctx.author.display_name}** found **{game.secret.upper()}** in {game.attempts_used}/{game.max_attempts}!",
                        show_keyboard=show_kb
                    )
                    self.bot.custom_games.pop(cid, None)
                    await ctx.send(embed=embed)

                elif game_over:
                    board_display = "\n".join([f"{h['pattern']}" for h in game.history])
                    reveal_text = f"The word was **{game.secret.upper()}**." if game.reveal_on_loss else "Better luck next time!"
                    embed = self._build_game_embed(
                        title="💀 GAME OVER",
                        color=discord.Color.red(),
                        board=board_display,
                        keypad=keypad,
                        footer=f"Attempts: {filled}{empty} | Custom mode (no rewards)",
                        header_text=reveal_text,
                        show_keyboard=show_kb
                    )
                    self.bot.custom_games.pop(cid, None)
                    await ctx.send(embed=embed)

                else:
                    embed = self._build_game_embed(
                        title=None,
                        color=discord.Color.gold(),
                        board=board_display,
                        keypad=keypad,
                        footer=format_attempt_footer(game.attempts_used, game.max_attempts),
                        header_text=f"Guessed `{g_word.upper()}`",
                        show_keyboard=show_kb
                    )
                    embed.set_author(
                        name=f"{ctx.author.display_name}",
                        icon_url=ctx.author.display_avatar.url
                    )
                    await ctx.send(embed=embed)
                return

            # ========= REGULAR GAME =========
            if win:
                winner_user = None
                for h in reversed(game.history):
                    if (h.get('word') or '').upper() == game.secret.upper():
                        winner_user = h.get('user')
                        break
                if winner_user is None:
                    winner_user = ctx.author

                # 1. Pop from games immediately to prevent double-processing
                # Use atomic check-and-remove to prevent race conditions
                if cid not in self.bot.games:
                    return  # Game already processed by another guess
                self.bot.games.pop(cid, None)

                # 2. Build and send INSTANT board embed
                # 2. Build and send INSTANT board embed
                final_time = (datetime.datetime.now() - game.start_time).total_seconds()
                from src.utils import get_win_flavor
                flavor = get_win_flavor(game.attempts_used)
                board_display = "\n".join([f"{h['pattern']}" for h in game.history])
                
                instant_embed = self._build_game_embed(
                    title=f"🏆 VICTORY!\n{flavor}",
                    color=discord.Color.green(),
                    board=board_display,
                    keypad=keypad,
                    footer=f"⏱️ Solved in {final_time:.1f}s",
                    header_text=f"**{winner_user.display_name}** found **{game.secret.upper()}** in {game.attempts_used}/6!",
                    show_keyboard=True
                )
                
                view = PlayAgainView(self.bot, is_classic=(game.difficulty == 1), hard_mode=getattr(game, 'hard_mode', False), is_win=True) if game.difficulty in [0, 1] else None
                
                # Send with retry logic for Discord API resilience
                for attempt in range(3):
                    try:
                        await ctx.send(embed=instant_embed, view=view)
                        break
                    except discord.HTTPException as e:
                        if e.status == 429 and attempt < 2:  # Rate limited
                            await asyncio.sleep(0.5 * (attempt + 1))
                        elif attempt == 2:
                            # Final attempt failed, log and continue
                            print(f"Failed to send win embed after 3 attempts: {e}")
                        else:
                            break
                    except Exception as e:
                        print(f"Error sending win embed: {e}")
                        break

                # 3. Launch background task for rewards/progression
                task = asyncio.create_task(self._finish_game_sequence(ctx, game, winner_user, cid, is_win=True, final_time=final_time))
                task.add_done_callback(self.bot._handle_task_exception)

            elif game_over:
                # 1. Pop from games immediately with race condition check
                if cid not in self.bot.games:
                    return  # Game already processed
                self.bot.games.pop(cid, None)

                # 2. Build and send INSTANT board embed
                board_display = "\n".join([f"{h['pattern']}" for h in game.history])
                instant_embed = self._build_game_embed(
                    title="💀 GAME OVER",
                    color=discord.Color.red(),
                    board=board_display,
                    keypad=keypad,
                    footer="Better luck next time!",
                    header_text=f"The word was **{game.secret.upper()}**.",
                    show_keyboard=True
                )
                
                view = PlayAgainView(self.bot, is_classic=(game.difficulty == 1), hard_mode=getattr(game, 'hard_mode', False), is_win=False) if game.difficulty in [0, 1] else None
                
                # Send with retry logic for Discord API resilience
                for attempt in range(3):
                    try:
                        await ctx.send(embed=instant_embed, view=view)
                        break
                    except discord.HTTPException as e:
                        if e.status == 429 and attempt < 2:  # Rate limited
                            await asyncio.sleep(0.5 * (attempt + 1))
                        elif attempt == 2:
                            print(f"Failed to send loss embed after 3 attempts: {e}")
                        else:
                            break
                    except Exception as e:
                        print(f"Error sending loss embed: {e}")
                        break

                # 3. Launch background task for rewards/progression
                task = asyncio.create_task(self._finish_game_sequence(ctx, game, None, cid, is_win=False))
                task.add_done_callback(self.bot._handle_task_exception)

            else:
                # Just a turn (REGULAR GAME)
                embed = self._build_game_embed(
                    title=None,
                    color=discord.Color.gold(),
                    board=board_display,
                    keypad=keypad,
                    footer=format_attempt_footer(game.attempts_used, game.max_attempts),
                    header_text=f"Guessed `{g_word.upper()}`",
                    show_keyboard=True
                )
                embed.set_author(
                    name=f"{ctx.author.display_name}",
                    icon_url=ctx.author.display_avatar.url
                )
                await ctx.send(embed=embed)
                
        except Exception as e:
            traceback.print_exc()
            try:
                await ctx.send(f"❌ Internal Error: {e}", ephemeral=True)
            except: pass

    async def handle_interaction_guess(self, interaction: discord.Interaction, word: str):
        adapter = _InteractionGuessContext(interaction)
        await self._handle_guess_ctx(adapter, word)

    @commands.hybrid_command(name="guess", aliases=["g", "G"], description="Guess a 5-letter word.")
    async def guess(self, ctx, word: str):
        await self._handle_guess_ctx(ctx, word)

    @app_commands.command(name="g", description="Short guess command.")
    @app_commands.describe(word="Your guess")
    async def g_short(self, interaction: discord.Interaction, word: str):
        await self.handle_interaction_guess(interaction, word)

    async def _finish_game_sequence(self, ctx, game, winner_user, cid, is_win: bool, final_time: float = None):
        """Background task for reward processing and progression embeds."""
        bot = self.bot
        try:
            announcements = []
            if is_win:
                main_embed, breakdown_embed, _, res, level_ups, tier_ups, results = await handle_game_win(
                    bot, game, ctx, winner_user, cid, include_board=False, final_time=final_time
                )
                
                if main_embed is None: return # Stopped

                if res:
                    if res.get('level_up'):
                        announcements.append(f"🔼 **LEVEL UP!** {winner_user.mention} is now **Level {res['level_up']}**!")
                    if res.get('tier_up'):
                        t_icon = EMOJIS.get(res['tier_up']['icon'], res['tier_up']['icon'])
                        announcements.append(f"🎉 **PROMOTION!** {winner_user.mention} reached **{t_icon} {res['tier_up']['name']}** Tier!")
            else:
                # Loss
                main_embed, breakdown_embed, _, level_ups, tier_ups, results = await handle_game_loss(
                    bot, game, ctx, cid, include_board=False
                )
            
            # 2. Progression Announcements (Shared for participants)
            for uid, lvl in level_ups:
                u = bot.get_user(uid)
                if u: announcements.append(f"🔼 **LEVEL UP!** {u.mention} is now **Level {lvl}**!")
            
            for uid, t_info in tier_ups:
                u = bot.get_user(uid)
                if u:
                    t_icon = EMOJIS.get(t_info['icon'], t_info['icon'])
                    announcements.append(f"🎉 **PROMOTION!** {u.mention} reached **{t_icon} {t_info['name']}** Tier!")

            # 3. Send reward/breakdown/progression (Shared)
            tasks = []
            if is_win and main_embed:
                tasks.append(ctx.channel.send(embed=main_embed))
            if breakdown_embed:
                tasks.append(ctx.channel.send(embed=breakdown_embed))
            if announcements:
                ann_embed = discord.Embed(title="✨ Progression Updates", description="\n".join(announcements), color=discord.Color.gold())
                tasks.append(ctx.channel.send(embed=ann_embed))
            
            if tasks: await asyncio.gather(*tasks)

        except Exception:
            traceback.print_exc()


async def setup(bot):
    await bot.add_cog(GuessHandler(bot))
