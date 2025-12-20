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
        g_word = word.lower().strip()

        print(f"DEBUG: Guess in Channel {cid}. Active: {list(self.bot.games.keys())} | Custom: {list(self.bot.custom_games.keys())}")

        # Check which game type is active
        is_custom = False
        if custom_game:
            game = custom_game
            is_custom = True
        elif not game:
            return await ctx.send("⚠️ No active game.", ephemeral=True)

        if game.is_duplicate(g_word):
            return await ctx.send(f"⚠️ **{g_word.upper()}** already guessed!", ephemeral=True)
        if len(g_word) != 5 or not g_word.isalpha():
            return await ctx.send("⚠️ 5 letters only.", ephemeral=True)
        if g_word not in self.bot.valid_set:
            return await ctx.send(f"⚠️ **{g_word.upper()}** not in dictionary.", ephemeral=True)

        pat, win, game_over = game.process_turn(g_word, ctx.author)

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
        empty = "○" * (6 - game.attempts_used)
        board_display = "\n".join([f"{h['pattern']}" for h in game.history])

        message_content = f"**Keyboard Status:**\n{keypad}"

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
                empty = "○" * (6 - game.attempts_used)
                board_display = "\n".join([f"{h['pattern']}" for h in game.history])

                embed = discord.Embed(
                    title="🏆 VICTORY!",
                    color=discord.Color.green()
                )
                embed.description = f"**{ctx.author.display_name}** found **{game.secret.upper()}** in {game.attempts_used}/6!"
                embed.add_field(name="Final Board", value=board_display, inline=False)
                embed.set_footer(text=f"Attempts: {filled}{empty} | Custom mode (no rewards)")

                # Clean up
                self.bot.custom_games.pop(cid, None)
                await ctx.send(content=message_content, embed=embed)

            elif game_over:
                # Game over - all attempts used
                filled = "●" * game.attempts_used
                empty = "○" * (6 - game.attempts_used)
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
                empty = "○" * (6 - game.attempts_used)
                board_display = "\n".join([f"{h['pattern']}" for h in game.history])
                
                embed = discord.Embed(title=f"Attempt {game.attempts_used}/6", color=discord.Color.gold())
                embed.description = f"**{ctx.author.display_name}{badge_str}** guessed: `{g_word.upper()}`"
                embed.add_field(name="Current Board", value=board_display, inline=False)
                embed.set_footer(text=f"{6 - game.attempts_used} tries left [{filled}{empty}]")
                await ctx.send(content=message_content, embed=embed)

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
            
            # Send main win embed and breakdown
            if res:
                if res.get('level_up'):
                    lvl = res['level_up']
                    await ctx.channel.send(f"🔼 **LEVEL UP!** {winner_user.mention} is now **Level {lvl}**! 🔼")

                if res.get('tier_up'):
                    t_name = res['tier_up']['name']
                    t_icon = res['tier_up']['icon']
                    await ctx.channel.send(f"🎉 **PROMOTION!** {winner_user.mention} has reached **{t_icon} {t_name}** Tier! 🎉")
            
            # Send level up messages for participants
            if level_ups:
                for uid, lvl in level_ups:
                    try:
                        # Optimization: get_user first
                        participant = self.bot.get_user(uid) or await self.bot.fetch_user(uid)
                        await ctx.channel.send(f"🔼 **LEVEL UP!** {participant.mention} is now **Level {lvl}**! 🔼")
                    except Exception:
                        pass

            # Send tier up messages for participants
            if tier_ups:
                for uid, tier_info in tier_ups:
                    try:
                        participant = self.bot.get_user(uid) or await self.bot.fetch_user(uid)
                        t_name = tier_info['name']
                        t_icon = tier_info['icon']
                        # Resolve icon if it's a key
                        from src.utils import EMOJIS
                        icon_display = EMOJIS.get(t_icon, t_icon)
                        await ctx.channel.send(f"🎉 **PROMOTION!** {participant.mention} has reached **{icon_display} {t_name}** Tier! 🎉")
                    except Exception:
                        pass

            self.bot.games.pop(cid, None)
            await ctx.send(content=message_content, embed=main_embed, view=view)

            # Send breakdown as separate message
            if breakdown_embed:
                try:
                    await ctx.channel.send(embed=breakdown_embed)
                except Exception:
                    pass

        elif game_over:
            # Handle loss: award all participants
            main_embed, participant_rows, level_ups, tier_ups = await handle_game_loss(self.bot, game, ctx, cid)

            # Add "Play Again" button for multiplayer games
            view = None
            if game.difficulty in [0, 1]:  # Simple or Classic
                view = PlayAgainView(self.bot, is_classic=(game.difficulty == 1))

            # Send level up messages for all participants
            if level_ups:
                for uid, lvl in level_ups:
                    try:
                        participant = self.bot.get_user(uid) or await self.bot.fetch_user(uid)
                        await ctx.channel.send(f"🔼 **LEVEL UP!** {participant.mention} is now **Level {lvl}**! 🔼")
                    except Exception:
                        pass

            # Send tier up messages for all participants
            if tier_ups:
                for uid, tier_info in tier_ups:
                    try:
                        participant = self.bot.get_user(uid) or await self.bot.fetch_user(uid)
                        t_name = tier_info['name']
                        t_icon = tier_info['icon']
                        from src.utils import EMOJIS
                        icon_display = EMOJIS.get(t_icon, t_icon)
                        await ctx.channel.send(f"🎉 **PROMOTION!** {participant.mention} has reached **{icon_display} {t_name}** Tier! 🎉")
                    except Exception:
                        pass

            self.bot.games.pop(cid, None)
            await ctx.send(content=message_content, embed=main_embed, view=view)

            # Optionally send breakdown for loss too (show participant rewards)
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
                            badge_map = {}

                    # Fetch all names concurrently using cache
                    name_tasks = [get_cached_username(self.bot, uid) for uid, *_ in participant_rows]
                    names = await asyncio.gather(*name_tasks)
                    
                    lines = []
                    for (uid, outcome_key, xp_v, wr_v), name in zip(participant_rows, names):
                        badge_key = badge_map.get(uid)
                        badge_emoji = get_badge_emoji(badge_key) if badge_key else ''
                        wr_part = f" | WR: {wr_v}" if wr_v is not None else ""
                        lines.append(f"{name} {badge_emoji} — {outcome_key.replace('_', ' ').title()}: +{xp_v} XP{wr_part}")

                    participants_text = "\n".join(lines)
                    if len(participants_text) > 900:
                        participants_text = participants_text[:900] + "\n..."
                    breakdown.add_field(name="Participants", value=participants_text, inline=False)

                breakdown.set_footer(text="Rewards applied instantly.")
                await ctx.channel.send(embed=breakdown)
            except Exception:
                pass

        else:
            # Just a turn
            embed = discord.Embed(title=f"Attempt {game.attempts_used}/6", color=discord.Color.gold())
            embed.description = f"**{ctx.author.display_name}{badge_str}** guessed: `{g_word.upper()}`"
            embed.add_field(name="Current Board", value=board_display, inline=False)
            embed.set_footer(text=f"{6 - game.attempts_used} tries left [{filled}{empty}]")
            await ctx.send(content=message_content, embed=embed)


async def setup(bot):
    await bot.add_cog(GuessHandler(bot))
