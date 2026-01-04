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
from src.utils import get_badge_emoji, get_cached_username, EMOJIS
from src.ui import get_markdown_keypad_status
from src.handlers.game_logic import handle_game_win, handle_game_loss, PlayAgainView
from src.database import trigger_egg


class GuessHandler(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _build_game_embed(self, title: str, color, board: str, keypad: str, footer: str, 
                          header_text: str = "", show_keyboard: bool = True) -> discord.Embed:
        """
        Build a unified game embed with board and keyboard in description.
        4096 char limit for description should handle 10 tries + keyboard easily.
        """
        embed = discord.Embed(title=title, color=color)
        
        # Build description with visual separators
        desc_parts = []
        if header_text:
            desc_parts.append(header_text)
        
        desc_parts.append(f"**Board:**\n{board}")
        
        if show_keyboard and keypad:
            desc_parts.append(f"**Keyboard:**\n{keypad}")
        
        embed.description = "\n\n".join(desc_parts)
        embed.set_footer(text=footer)
        return embed

    async def _delayed_ephemeral_streak(self, ctx, user, message, delay=2):
        """Helper to send delayed, private streak/badge updates."""
        await asyncio.sleep(delay)
        if hasattr(ctx, 'interaction') and ctx.interaction:
            try:
                # If interaction is already responded to, use followup
                await ctx.interaction.followup.send(content=message, ephemeral=True)
                return
            except:
                pass
        
        # Fallback for prefix commands or failed followup: Private DM
        try:
            await user.send(message)
        except:
            pass

    @commands.hybrid_command(name="guess", aliases=["g"], description="Guess a 5-letter word.")
    async def guess(self, ctx, word: str):
        await ctx.defer()
        if not ctx.guild:
            return await ctx.send("Guild only.", ephemeral=True)

        cid = ctx.channel.id
        
        try:
            game = self.bot.games.get(cid)
            custom_game = self.bot.custom_games.get(cid)
            g_word = word.strip().lower()

            print(f"DEBUG: Guess '{g_word}' | Channel {cid} | Active: {len(self.bot.games)} | Custom: {len(self.bot.custom_games)}")

            # Check which game type is active
            is_custom = False
            if custom_game:
                game = custom_game
                is_custom = True
                print(f"DEBUG: Custom Game | Max Attempts: {game.max_attempts}")
            elif not game:
                return await ctx.send("⚠️ No active game.", ephemeral=True)
            
            # Custom game validations
            if is_custom:
                if game.allowed_players and ctx.author.id not in game.allowed_players:
                    return await ctx.send("❌ This game is restricted to a specific player!", ephemeral=True)
                
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
            print(f"DEBUG: Turn | Attempts: {game.attempts_used}/{game.max_attempts} | Win: {win} | Over: {game_over}")

            # Easter Egg trigger (rate-limited)
            try:
                now_ts = datetime.datetime.now().timestamp()
                last = self.bot.egg_cooldowns.get(ctx.author.id, 0)
                COOLDOWN = 600
                if now_ts - last >= COOLDOWN:
                    self.bot.egg_cooldowns[ctx.author.id] = now_ts
                    egg = None
                    is_classic = game.secret in getattr(self.bot, 'hard_secrets', [])

                    if is_classic:
                        if random.randint(1, 1000) == 1: egg = 'dragon'
                        elif random.randint(1, 100) == 1: egg = 'candy'
                    else:
                        if random.randint(1, 100) == 1: egg = 'duck'
                        elif random.randint(1, 100) == 1: egg = 'candy'

                    if egg:
                        egg_emoji = EMOJIS.get(egg, '🎉')
                        try:
                            asyncio.create_task(asyncio.to_thread(trigger_egg, self.bot, ctx.author.id, egg))
                        except: pass
                        try:
                            await ctx.channel.send(f"{egg_emoji} **{ctx.author.display_name}** found a **{egg.title()}**! Added to collection.")
                        except: pass
            except: pass

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
                board_display = "\n".join(lines)
            else:
                board_display = "\n".join([h['pattern'] for h in game.history])

            # Fetch player badge
            try:
                b_res = self.bot.supabase_client.table('user_stats_v2').select('active_badge').eq('user_id', ctx.author.id).execute()
                active_badge = b_res.data[0]['active_badge'] if b_res.data else None
            except:
                active_badge = None
            badge_str = f" {get_badge_emoji(active_badge)}" if active_badge else ""

            # Determine if keyboard should be shown
            show_kb = (not is_custom) or game.show_keyboard

            if is_custom:
                # ========= CUSTOM GAME =========
                if win:
                    board_display = "\n".join([h['pattern'] for h in game.history])
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
                    board_display = "\n".join([h['pattern'] for h in game.history])
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
                        title=f"Attempt {game.attempts_used}/{game.max_attempts}",
                        color=discord.Color.gold(),
                        board=board_display,
                        keypad=keypad,
                        footer=f"{game.max_attempts - game.attempts_used} tries left {progress}",
                        header_text=f"**{ctx.author.display_name}{badge_str}** guessed: `{g_word.upper()}`",
                        show_keyboard=show_kb
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

                main_embed, breakdown_embed, _, res, level_ups, tier_ups = await handle_game_win(
                    self.bot, game, ctx, winner_user, cid
                )

                if main_embed is None:
                    return await ctx.send("⚠️ This game was stopped early — no rewards are given.")
                
                # Inject keyboard into main embed description
                if main_embed.description:
                    main_embed.description += f"\n\n**Keyboard:**\n{keypad}"
                else:
                    main_embed.description = f"**Keyboard:**\n{keypad}"
                
                view = PlayAgainView(self.bot, is_classic=(game.difficulty == 1), hard_mode=getattr(game, 'hard_mode', False)) if game.difficulty in [0, 1] else None
                
                announcements = []
                if res:
                    # Streak and Badge announcements are now handled separately (ephemeral + delayed)
                    if res.get('streak_msg'):
                        asyncio.get_event_loop().create_task(self._delayed_ephemeral_streak(ctx, winner_user, res['streak_msg']))
                    if res.get('streak_badge'):
                        asyncio.get_event_loop().create_task(self._delayed_ephemeral_streak(ctx, winner_user, f"💎 **BADGE UNLOCKED:** {get_badge_emoji(res['streak_badge'])} Badge!", delay=3))

                    if res.get('level_up'):
                        announcements.append(f"🔼 **LEVEL UP!** {winner_user.mention} is now **Level {res['level_up']}**!")
                    if res.get('tier_up'):
                        t_icon = EMOJIS.get(res['tier_up']['icon'], res['tier_up']['icon'])
                        announcements.append(f"🎉 **PROMOTION!** {winner_user.mention} reached **{t_icon} {res['tier_up']['name']}** Tier!")
                
                for uid, outcome_key, pres in results:
                    if pres:
                        p_user = self.bot.get_user(uid)
                        if p_user:
                            if pres.get('streak_msg'):
                                asyncio.get_event_loop().create_task(self._delayed_ephemeral_streak(ctx, p_user, pres['streak_msg']))
                            if pres.get('streak_badge'):
                                asyncio.get_event_loop().create_task(self._delayed_ephemeral_streak(ctx, p_user, f"💎 **BADGE UNLOCKED:** {get_badge_emoji(pres['streak_badge'])} Badge!", delay=3))

                for uid, lvl in level_ups:
                    u = self.bot.get_user(uid)
                    if u: announcements.append(f"🔼 **LEVEL UP!** {u.mention} is now **Level {lvl}**!")
                
                for uid, t_info in tier_ups:
                    u = self.bot.get_user(uid)
                    if u:
                        t_icon = EMOJIS.get(t_info['icon'], t_info['icon'])
                        announcements.append(f"🎉 **PROMOTION!** {u.mention} reached **{t_icon} {t_info['name']}** Tier!")

                tasks = [ctx.send(embed=main_embed, view=view)]
                if breakdown_embed:
                    tasks.append(ctx.channel.send(embed=breakdown_embed))
                if announcements:
                    ann_embed = discord.Embed(title="✨ Progression Updates", description="\n".join(announcements), color=discord.Color.gold())
                    tasks.append(ctx.channel.send(embed=ann_embed))

                self.bot.games.pop(cid, None)
                await asyncio.gather(*tasks)

            elif game_over:
                main_embed, participant_rows, level_ups, tier_ups = await handle_game_loss(self.bot, game, ctx, cid)

                # Inject keyboard into main embed
                if main_embed.description:
                    main_embed.description += f"\n\n**Keyboard:**\n{keypad}"
                else:
                    main_embed.description = f"**Keyboard:**\n{keypad}"

                view = PlayAgainView(self.bot, is_classic=(game.difficulty == 1), hard_mode=getattr(game, 'hard_mode', False)) if game.difficulty in [0, 1] else None

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
                            except: pass

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
                except: pass

                tasks = [ctx.send(embed=main_embed, view=view)]
                if breakdown:
                    tasks.append(ctx.channel.send(embed=breakdown))
                if announcements:
                    ann_embed = discord.Embed(title="✨ Progression Updates", description="\n".join(announcements), color=discord.Color.gold())
                    tasks.append(ctx.channel.send(embed=ann_embed))

                await asyncio.gather(*tasks)

            else:
                # Just a turn (REGULAR GAME)
                embed = self._build_game_embed(
                    title=f"Attempt {game.attempts_used}/{game.max_attempts}",
                    color=discord.Color.gold(),
                    board=board_display,
                    keypad=keypad,
                    footer=f"{game.max_attempts - game.attempts_used} tries left {progress}",
                    header_text=f"**{ctx.author.display_name}{badge_str}** guessed: `{g_word.upper()}`",
                    show_keyboard=True
                )
                await ctx.send(embed=embed)
                
        except Exception as e:
            traceback.print_exc()
            try:
                await ctx.send(f"❌ Internal Error: {e}", ephemeral=True)
            except: pass


async def setup(bot):
    await bot.add_cog(GuessHandler(bot))
