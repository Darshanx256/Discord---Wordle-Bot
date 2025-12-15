"""
Guess handler cog: /guess command with win/loss logic
"""
import asyncio
import datetime
import discord
from discord.ext import commands
from src.utils import get_badge_emoji
from src.ui import get_markdown_keypad_status
from src.handlers.game_logic import handle_game_win, handle_game_loss


class GuessHandler(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="guess", description="Guess a 5-letter word.")
    async def guess(self, ctx, word: str):
        await ctx.defer()
        if not ctx.guild:
            return await ctx.send("Guild only.", ephemeral=True)

        cid = ctx.channel.id
        game = self.bot.games.get(cid)
        g_word = word.lower().strip()

        print(f"DEBUG: Guess in Channel {cid}. Active: {list(self.bot.games.keys())}")

        if not game:
            return await ctx.send("⚠️ No active game.", ephemeral=True)
        if game.is_duplicate(g_word):
            return await ctx.send(f"⚠️ **{g_word.upper()}** already guessed!", ephemeral=True)
        if len(g_word) != 5 or not g_word.isalpha():
            return await ctx.send("⚠️ 5 letters only.", ephemeral=True)
        if g_word not in self.bot.valid_set:
            return await ctx.send(f"⚠️ **{g_word.upper()}** not in dictionary.", ephemeral=True)

        pat, win, game_over = game.process_turn(g_word, ctx.author)

        keypad = get_markdown_keypad_status(game.used_letters, self.bot, ctx.author.id)
        filled = "●" * game.attempts_used
        empty = "○" * (6 - game.attempts_used)
        board_display = "\n".join([f"{h['pattern']}" for h in game.history])

        message_content = f"⌨️ **Keyboard Status:**\n{keypad}"

        # Fetch player badge
        try:
            b_res = self.bot.supabase_client.table('user_stats_v2').select('active_badge').eq('user_id', ctx.author.id).execute()
            active_badge = b_res.data[0]['active_badge'] if b_res.data else None
        except:
            active_badge = None

        badge_str = f" {get_badge_emoji(active_badge)}" if active_badge else ""

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
            main_embed, breakdown_embed, _, res = await handle_game_win(
                self.bot, game, ctx, winner_user, cid
            )

            if main_embed is None:
                # Game was stopped early
                return await ctx.send("⚠️ This game was stopped early — no rewards are given.")

            # Send main win embed and breakdown
            if res:
                if res.get('level_up'):
                    lvl = res['level_up']
                    await ctx.channel.send(f"🔼 **LEVEL UP!** {winner_user.mention} is now **Level {lvl}**! 🔼")

                if res.get('tier_up'):
                    t_name = res['tier_up']['name']
                    t_icon = res['tier_up']['icon']
                    await ctx.channel.send(f"🎉 **PROMOTION!** {winner_user.mention} has reached **{t_icon} {t_name}** Tier! 🎉")

            self.bot.games.pop(cid, None)
            await ctx.send(content=message_content, embed=main_embed)

            # Send breakdown as separate message
            if breakdown_embed:
                try:
                    await ctx.channel.send(embed=breakdown_embed)
                except Exception:
                    pass

        elif game_over:
            # Handle loss: award all participants
            main_embed, participant_rows = await handle_game_loss(self.bot, game, ctx, cid)

            self.bot.games.pop(cid, None)
            await ctx.send(content=message_content, embed=main_embed)

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

                    lines = []
                    for uid, outcome_key, xp_v, wr_v in participant_rows:
                        member = self.bot.get_user(uid)
                        name = getattr(member, 'display_name', str(uid))
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
