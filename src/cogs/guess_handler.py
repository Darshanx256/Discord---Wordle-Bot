"""
Guess handler cog: /guess command with win/loss logic
"""
import asyncio
import datetime
import random
import discord
from discord.ext import commands
from src.utils import get_badge_emoji, get_cached_username, EMOJIS
from src.ui import get_markdown_keypad_status
from src.handlers.game_logic import handle_game_win, handle_game_loss, PlayAgainView
from src.database import trigger_egg


class GuessHandler(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="guess", aliases=["g"], description="Guess a 5-letter word.")
    async def guess(self, ctx, word: str):
        await ctx.defer()
        if not ctx.guild:
            return await ctx.send("Guild only.", ephemeral=True)

        cid = ctx.channel.id
        game = self.bot.games.get(cid)
        custom_game = self.bot.custom_games.get(cid)
        # Strip all whitespace first (handles "-g     dance" case)
        g_word = word.strip().lower()

        print(f"DEBUG: Guess '{g_word}' | Channel {cid} | Active Games: {len(self.bot.games)} | Custom: {len(self.bot.custom_games)}")

        # Check which game type is active
        is_custom = False
        if custom_game:
            game = custom_game
            is_custom = True
        elif not game:
            return await ctx.send("⚠️ No active game.", ephemeral=True)
        
        # Custom game validations
        if is_custom:
            # Check if player is restricted
            if game.allowed_player_id and ctx.author.id != game.allowed_player_id:
                return await ctx.send(
                    "❌ This game is restricted to a specific player!",
                    ephemeral=True
                )
            
            # Check custom dictionary if set
            if game.custom_dict and g_word not in game.custom_dict:
                return await ctx.send(
                    f"⚠️ **{g_word.upper()}** not in custom dictionary!",
                    ephemeral=True
                )

        if game.is_duplicate(g_word):
            return await ctx.send(f"⚠️ **{g_word.upper()}** already guessed!", ephemeral=True)
        if len(g_word) != 5 or not g_word.isalpha():
            return await ctx.send("⚠️ 5 letters only.", ephemeral=True)
        
        # Use custom dictionary if set, otherwise use bot's valid set
        valid_check = game.custom_dict if (is_custom and game.custom_dict) else self.bot.valid_set
        if g_word not in valid_check:
            return await ctx.send(f"⚠️ **{g_word.upper()}** not in dictionary.", ephemeral=True)

        pat, win, game_over = game.process_turn(g_word, ctx.author)
        print(f"DEBUG: Processed Turn | Attempts: {game.attempts_used}/{game.max_attempts} | Win: {win} | Over: {game_over}")

        # Attempt Easter Egg trigger (rate-limited per-user to avoid farming)
        try:
            now_ts = datetime.datetime.now().timestamp()
            last = self.bot.egg_cooldowns.get(ctx.author.id, 0)
            COOLDOWN = 600  # seconds per user between egg attempts
            if now_ts - last >= COOLDOWN:
                # update last attempt time immediately to prevent races
                self.bot.egg_cooldowns[ctx.author.id] = now_ts

                egg = None
                egg_emoji = None
                # Classic vs Simple detection
                is_classic = game.secret in getattr(self.bot, 'hard_secrets', [])

                if is_classic:
                    # Classic mode: dragon 1/1000, candy 1/100
                    if random.randint(1, 1000) == 1:
                        egg = 'dragon'
                    elif random.randint(1, 100) == 1:
                        egg = 'candy'
                else:
                    # Simple mode: duck 1/100, candy 1/100
                    if random.randint(1, 100) == 1:
                        egg = 'duck'
                    elif random.randint(1, 100) == 1:
                        egg = 'candy'

                if egg:
                    egg_emoji = EMOJIS.get(egg, '🎉')
                    # Trigger DB update in background thread (increments eggs dict)
                    try:
                        asyncio.create_task(asyncio.to_thread(trigger_egg, self.bot, ctx.author.id, egg))
                    except Exception:
                        pass

                    # Notify channel with custom emoji
                    try:
                        egg_display_name = egg.replace('_', ' ').title()
                        await ctx.channel.send(f"{egg_emoji} **{ctx.author.display_name}** found a **{egg_display_name}**! It has been added to your collection.")
                    except Exception:
                        pass
        except Exception:
            pass

        keypad = get_markdown_keypad_status(game.used_letters, self.bot, ctx.author.id)
        filled = "●" * game.attempts_used
        if is_custom and getattr(game, 'blind_mode', False) and not (win or game_over):
            # Blind mode: Hide Yellows/Greys, show Greens
            # We reconstruct the line row by row
            lines = []
            for h in game.history:
                pat = h['pattern'] 
                # pat is a string of emojis. We can't easy regex replace emojis universally safely.
                # BETTER APPROACH: We have h['pattern'] but we need original states to reconstruct "masked".
                # However, game.history logic in 'game.py' stores the final emoji string in 'pattern'.
                # Re-evaluating is expensive/complex here without access to the class logic.
                # LUCKILY: The pattern string is just concatenated emojis.
                # But 'pattern' doesn't tell us which char was green easily without parsing the emoji string? 
                # Actually, 'WordleGame.evaluate_guess' returns the emoji string.
                # We can't easily "replace not green with black" on the emoji string itself.
                
                # ALTERNATIVE: Re-evaluate logic?
                # or modify 'game.py' to store state.
                # Actually, blindly masking everything was easier.
                # If the user wants to see Greens, we need to know WHICH are greens.
                
                # Check line 100 in game.py: history stores 'word', 'pattern', 'user'.
                # We can re-evaluate quickly since we have game.secret and h['word'].
                # Since we are in 'GuessHandler', we can re-derive the pattern manually for display.
                
                guess_word = h['word']
                secret_word = game.secret
                # Simple re-check for Greens
                masked_line = ""
                for i, char in enumerate(guess_word):
                    if char.upper() == secret_word[i].upper():
                        # It's Green. Show the Green Emoji for this char
                        # We need the utils EMOJIS dict or similar helper
                        # Assuming 'block_X_green' exists
                         masked_line += EMOJIS.get(f"block_{char.lower()}_green", "🟩")
                    else:
                         # Mask everything else (Yellow or Grey) as Black/Unknown
                         # Using a generic black block emoji
                         masked_line += "⬛"
                lines.append(masked_line)
            board_display = "\n".join(lines)
        else:
            board_display = "\n".join([f"{h['pattern']}" for h in game.history])

        empty = "○" * (game.max_attempts - game.attempts_used)

        message_content = f"**Keyboard Status:**\n{keypad}" if (not is_custom or game.show_keyboard) else ""

        # Fetch player badge
        try:
            b_res = self.bot.supabase_client.table('user_stats_v2').select('active_badge').eq('user_id', ctx.author.id).execute()
            active_badge = b_res.data[0]['active_badge'] if b_res.data else None
        except:
            active_badge = None

        badge_str = f" {get_badge_emoji(active_badge)}" if active_badge else ""

        if is_custom:
            # ========= CUSTOM GAME LOGIC =========
            if win:
                # Winner found the word
                filled = "●" * game.attempts_used
                empty = "○" * (game.max_attempts - game.attempts_used)
                board_display = "\n".join([f"{h['pattern']}" for h in game.history])

                embed = discord.Embed(
                    title="🏆 VICTORY!",
                    color=discord.Color.green()
                )
                embed.description = f"**{ctx.author.display_name}** found **{game.secret.upper()}** in {game.attempts_used}/{game.max_attempts}!"
                embed.add_field(name="Final Board", value=board_display, inline=False)
                embed.set_footer(text=f"Attempts: {filled}{empty} | Custom mode (no rewards)")

                # Clean up
                self.bot.custom_games.pop(cid, None)
                await ctx.send(content=message_content, embed=embed)

            elif game_over:
                # Game over - all attempts used
                filled = "●" * game.attempts_used
                empty = "○" * (game.max_attempts - game.attempts_used)
                board_display = "\n".join([f"{h['pattern']}" for h in game.history])

                embed = discord.Embed(
                    title="💀 GAME OVER",
                    color=discord.Color.red()
                )
                
                # Check if reveal was enabled
                reveal_text = f"The word was **{game.secret.upper()}**." if game.reveal_on_loss else "Better luck next time!"
                embed.description = reveal_text
                embed.add_field(name="Final Board", value=board_display, inline=False)
                embed.set_footer(text=f"Attempts: {filled}{empty} | Custom mode (no rewards)")

                # Clean up
                self.bot.custom_games.pop(cid, None)
                await ctx.send(content=message_content, embed=embed)

            else:
                # Just a turn in custom game
                filled = "●" * game.attempts_used
                empty = "○" * (game.max_attempts - game.attempts_used)
                board_display = "\n".join([f"{h['pattern']}" for h in game.history])
                
                embed = discord.Embed(title=f"Attempt {game.attempts_used}/{game.max_attempts}", color=discord.Color.gold())
                embed.description = f"**{ctx.author.display_name}{badge_str}** guessed: `{g_word.upper()}`"
                embed.add_field(name="Current Board", value=board_display, inline=False)
                embed.set_footer(text=f"{game.max_attempts - game.attempts_used} tries left [{filled}{empty}]")
                # Only send keyboard if show_keyboard is True
                content = message_content if game.show_keyboard else None
                await ctx.send(content=content, embed=embed)

            return  # Exit - no DB recording for custom mode

        # ========= REGULAR GAME LOGIC =========
        if win:
            # Identify actual winner from history
            winner_user = None
            for h in reversed(game.history):
                if (h.get('word') or '').upper() == game.secret.upper():
                    winner_user = h.get('user')
                    break

            if winner_user is None:
                winner_user = ctx.author

            # Handle win: award winner + participants, send breakdown
            main_embed, breakdown_embed, _, res, level_ups, tier_ups = await handle_game_win(
                self.bot, game, ctx, winner_user, cid
            )

            if main_embed is None:
                # Game was stopped early
                return await ctx.send("⚠️ This game was stopped early — no rewards are given.")
            
            # Add "Play Again" button for multiplayer games
            view = None
            if game.difficulty in [0, 1]:  # Simple or Classic
                view = PlayAgainView(self.bot, is_classic=(game.difficulty == 1))
            
            # Collect all announcements for bundling
            announcements = []
            
            # Winner progression
            if res:
                if res.get('level_up'):
                    announcements.append(f"🔼 **LEVEL UP!** {winner_user.mention} is now **Level {res['level_up']}**!")
                if res.get('tier_up'):
                    t_icon = EMOJIS.get(res['tier_up']['icon'], res['tier_up']['icon'])
                    announcements.append(f"🎉 **PROMOTION!** {winner_user.mention} reached **{t_icon} {res['tier_up']['name']}** Tier!")
            
            # Participants progression
            # Optimization: Pre-fetch all user objects in parallel if needed, but get_user is fast
            for uid, lvl in level_ups:
                u = self.bot.get_user(uid)
                if u: announcements.append(f"🔼 **LEVEL UP!** {u.mention} is now **Level {lvl}**!")
            
            for uid, t_info in tier_ups:
                u = self.bot.get_user(uid)
                if u:
                    t_icon = EMOJIS.get(t_info['icon'], t_info['icon'])
                    announcements.append(f"🎉 **PROMOTION!** {u.mention} reached **{t_icon} {t_info['name']}** Tier!")

            # Prepare final sends
            tasks = []
            
            # 1. Main victory embed (with Play Again view)
            tasks.append(ctx.send(content=message_content, embed=main_embed, view=view))
            
            # 2. Breakdown embed (if any)
            if breakdown_embed:
                tasks.append(ctx.channel.send(embed=breakdown_embed))
                
            # 3. Bundled announcements (if any)
            if announcements:
                ann_embed = discord.Embed(title="✨ Progression Updates", description="\n".join(announcements), color=discord.Color.gold())
                tasks.append(ctx.channel.send(embed=ann_embed))

            self.bot.games.pop(cid, None)
            await asyncio.gather(*tasks)

        elif game_over:
            # Handle loss: award all participants
            main_embed, participant_rows, level_ups, tier_ups = await handle_game_loss(self.bot, game, ctx, cid)

            # Add "Play Again" button for multiplayer games
            view = None
            if game.difficulty in [0, 1]:  # Simple or Classic
                view = PlayAgainView(self.bot, is_classic=(game.difficulty == 1))

            # Bundled Announcements
            announcements = []
            for uid, lvl in level_ups:
                u = self.bot.get_user(uid)
                if u: announcements.append(f"🔼 **LEVEL UP!** {u.mention} is now **Level {lvl}**!")
            for uid, t_info in tier_ups:
                u = self.bot.get_user(uid)
                if u:
                    t_icon = EMOJIS.get(t_info['icon'], t_info['icon'])
                    announcements.append(f"🎉 **PROMOTION!** {u.mention} reached **{t_icon} {t_info['name']}** Tier!")

            self.bot.games.pop(cid, None)
            
            # Logic for Loss breakdown (existing)
            breakdown = None
            try:
                breakdown = discord.Embed(title="🎖️ Game Over - Rewards", color=discord.Color.greyple())
                if participant_rows:
                    all_uids = [uid for uid, *_ in participant_rows]
                    badge_map = {}
                    if all_uids:
                        try:
                            b_resp = self.bot.supabase_client.table('user_stats_v2').select('user_id, active_badge').in_('user_id', all_uids).execute()
                            if b_resp.data:
                                for r in b_resp.data:
                                    badge_map[r['user_id']] = r.get('active_badge')
                        except:
                            pass

                    name_tasks = [get_cached_username(self.bot, uid) for uid, *_ in participant_rows]
                    names = await asyncio.gather(*name_tasks)
                    
                    lines = []
                    for (uid, outcome_key, xp_v, wr_v), name in zip(participant_rows, names):
                        badge_key = badge_map.get(uid)
                        badge_emoji = get_badge_emoji(badge_key) if badge_key else ''
                        wr_part = f" | WR: {wr_v}" if wr_v is not None else ""
                        lines.append(f"{name} {badge_emoji} — {outcome_key.replace('_', ' ').title()}: +{xp_v} XP{wr_part}")

                    participants_text = "\n".join(lines)
                    if len(participants_text) > 900: participants_text = participants_text[:900] + "\n..."
                    breakdown.add_field(name="Participants", value=participants_text, inline=False)
                breakdown.set_footer(text="Rewards applied instantly.")
            except:
                pass

            # Prepare final sends
            tasks = []
            tasks.append(ctx.send(content=message_content, embed=main_embed, view=view))
            if breakdown:
                tasks.append(ctx.channel.send(embed=breakdown))
            if announcements:
                ann_embed = discord.Embed(title="✨ Progression Updates", description="\n".join(announcements), color=discord.Color.gold())
                tasks.append(ctx.channel.send(embed=ann_embed))

            await asyncio.gather(*tasks)

        else:
            # Just a turn
            embed = discord.Embed(title=f"Attempt {game.attempts_used}/6", color=discord.Color.gold())
            embed.description = f"**{ctx.author.display_name}{badge_str}** guessed: `{g_word.upper()}`"
            embed.add_field(name="Current Board", value=board_display, inline=False)
            embed.set_footer(text=f"{6 - game.attempts_used} tries left [{filled}{empty}]")
            await ctx.send(content=message_content, embed=embed)


async def setup(bot):
    await bot.add_cog(GuessHandler(bot))
