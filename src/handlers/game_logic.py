"""
Shared game logic for handling wins, losses, and reward distribution in multiplayer games.
"""
import asyncio
import datetime
import discord
from src.game import WordleGame
from src.database import record_game_v2, get_next_word_bitset
from src.utils import get_badge_emoji, get_win_flavor, get_cached_username


async def _batch_fetch_participant_stats(bot, participant_ids: list):
    """Fetch WR, XP, Badges, and Daily Gains for multiple users in bulk."""
    stats_map = {} # {uid: {'wr': 1200, 'xp': 0, 'badge': '...', 'daily': 0}}
    if not participant_ids:
        return stats_map
        
    try:
        # 1. Fetch WR, XP and Badges
        s_res = bot.supabase_client.table('user_stats_v2').select('user_id, multi_wr, xp, active_badge').in_('user_id', participant_ids).execute()
        for r in s_res.data:
            stats_map[r['user_id']] = {'wr': r['multi_wr'], 'xp': r['xp'], 'badge': r['active_badge'], 'daily': 0}
            
        # 2. Fetch Daily Gains
        today_start = datetime.datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        h_res = bot.supabase_client.table('match_history').select('user_id, wr_delta').in_('user_id', participant_ids).gte('created_at', today_start.isoformat()).gt('wr_delta', 0).execute()
        for r in h_res.data:
            uid = r['user_id']
            if uid in stats_map:
                stats_map[uid]['daily'] += r['wr_delta']
    except Exception as e:
        print(f"⚠️ Batch fetch error: {e}")
    return stats_map

def _calculate_user_greens(history, secret: str):
    """Calculate unique greens discovered by each participant."""
    user_unique_greens = {} # {uid: count}
    discovered_indices = set()
    
    for h in history:
        uid = getattr(h.get('user'), 'id', None)
        if not uid: continue
        
        guess = h.get('word', '') or ''
        # Check greens
        for i, (g_char, s_char) in enumerate(zip(guess.upper(), secret.upper())):
            if g_char == s_char:
                if i not in discovered_indices:
                    discovered_indices.add(i)
                    user_unique_greens[uid] = user_unique_greens.get(uid, 0) + 1
    return user_unique_greens

async def _process_participant_reward(bot, uid, outcome_key, attempts, time_taken, stats_map, guild_id):
    """Shared logic to simulate and record rewards for a participant."""
    p_stats = stats_map.get(uid, {'wr': 1200, 'xp': 0, 'badge': None, 'daily': 0})
    
    from src.database import simulate_record_game
    # Simulate local result
    pres = simulate_record_game(
        bot, uid, 'MULTI', outcome_key, 
        attempts, time_taken, pre_wr=p_stats['wr'], pre_xp=p_stats['xp'], pre_daily=p_stats['daily']
    )
    
    # Background DB update
    asyncio.create_task(asyncio.to_thread(
        record_game_v2, bot, uid, guild_id, 'MULTI', outcome_key, 
        attempts, time_taken, pre_wr=p_stats['wr'], pre_daily=p_stats['daily']
    ))
    
    return (uid, outcome_key, pres)

async def _build_breakdown_embed(bot, participant_rows, winner_data=None, badge_map=None):
    """Build a unified rewards breakdown embed."""
    breakdown = discord.Embed(title="🎖️ Rewards Summary", color=discord.Color.blurple())
    
    if winner_data:
        breakdown.add_field(
            name="Winner",
            value=f"{winner_data['name']}{winner_data['badge_emoji']} — +{winner_data['xp_gain']} XP | WR: {winner_data['multi_wr']}",
            inline=False
        )

    if participant_rows:
        # Use provided badge_map or default to empty
        badges = badge_map or {}

        # Fetch all names concurrently using cache
        name_tasks = [get_cached_username(bot, uid) for uid, *_ in participant_rows]
        names = await asyncio.gather(*name_tasks)
        
        lines = []
        for (uid, outcome_key, xp_v, wr_v), name in zip(participant_rows, names):
            badge_key = badges.get(uid)
            badge_emoji = get_badge_emoji(badge_key) if badge_key else ''
            wr_part = f" | WR: {wr_v}" if wr_v is not None else ""
            lines.append(f"{name} {badge_emoji} — {outcome_key.replace('_', ' ').title()}: +{xp_v} XP{wr_part}")

        participants_text = "\n".join(lines)
        if len(participants_text) > 900:
            participants_text = participants_text[:900] + "\n..."
        breakdown.add_field(name="Participants", value=participants_text, inline=False)

    breakdown.set_footer(text="Rewards applied instantly.")
    return breakdown


async def _process_game_results(bot, game, winner_user, guild_id, time_taken, include_board: bool, is_win: bool):
    """
    Common orchestrator for win/loss reward processing.
    Handles stats fetching, green counting, reward recording, and breakdown building.
    """
    # 1. BATCH FETCH STATS
    all_participants = list(game.participants)
    stats_map = await _batch_fetch_participant_stats(bot, all_participants)

    winner_stats = stats_map.get(winner_user.id, {'wr': 1200, 'xp': 0, 'badge': None, 'daily': 0}) if winner_user else None
    
    # 2. Build Main Embed
    if is_win:
        flavor = get_win_flavor(game.attempts_used)
        embed = discord.Embed(title=f"🏆 VICTORY!\n{flavor}", color=discord.Color.green())
        win_badge = winner_stats['badge'] if winner_stats else None
        win_badge_str = f" {get_badge_emoji(win_badge)}" if win_badge else ""
        
        if include_board:
            board_display = "\n".join([f"{h['pattern']}" for h in game.history])
            embed.description = f"**{winner_user.mention}{win_badge_str}** found **{game.secret.upper()}** in {game.attempts_used}/6!"
            embed.add_field(name="Final Board", value=board_display, inline=False)
            embed.set_footer(text=f"⏱️ Solved in {time_taken:.1f}s")
        else:
            embed.description = f"**{winner_user.mention}{win_badge_str}** won the game!"
            embed.title = "✨ Game Rewards"
            embed.color = discord.Color.gold()
    else:
        embed = discord.Embed(title="💀 GAME OVER", color=discord.Color.red())
        embed.description = f"The word was **{game.secret.upper()}**."
        if include_board:
            board_display = "\n".join([f"{h['pattern']}" for h in game.history])
            embed.add_field(name="Final Board", value=board_display, inline=False)

    # 3. Simulate & Record Winners
    winner_res = None
    if is_win and winner_user:
        from src.database import simulate_record_game
        winner_res = simulate_record_game(
            bot, winner_user.id, 'MULTI', 'win', 
            game.attempts_used, time_taken, 
            pre_wr=winner_stats['wr'], pre_xp=winner_stats['xp'], pre_daily=winner_stats['daily']
        )
        asyncio.create_task(asyncio.to_thread(
            record_game_v2, bot, winner_user.id, guild_id, 'MULTI', 'win', 
            game.attempts_used, time_taken, 
            pre_wr=winner_stats['wr'], pre_daily=winner_stats['daily']
        ))
        if winner_res:
            embed.add_field(name="Winner Rewards", value=f"+ {winner_res.get('xp_gain', 0)} XP | 📈 WR: {winner_res.get('multi_wr')}", inline=False)

    # 4. Award participants
    user_unique_greens = _calculate_user_greens(game.history, game.secret)
    others = list(game.participants - {winner_user.id}) if is_win and winner_user else list(game.participants)

    def get_outcome_key(uid):
        ug = user_unique_greens.get(uid, 0)
        if ug >= 5: return 'win'
        if ug == 4: return 'correct_4'
        if ug == 3: return 'correct_3'
        if ug == 2: return 'correct_2'
        if ug == 1: return 'correct_1'
        return 'participation'

    results = []
    if others:
        results = await asyncio.gather(*(
            _process_participant_reward(bot, uid, get_outcome_key(uid), game.attempts_used if is_win else 6, 999, stats_map, guild_id)
            for uid in others
        ))

    # 5. Build Breakdown
    participant_rows = []
    level_ups = []
    tier_ups = []
    for uid, outcome_key, pres in results:
        if pres:
            if pres.get('level_up'): level_ups.append((uid, pres['level_up']))
            if pres.get('tier_up'): tier_ups.append((uid, pres['tier_up']))
            participant_rows.append((uid, outcome_key, pres.get('xp_gain', 0), pres.get('multi_wr')))

    winner_data = None
    if is_win and winner_user:
        winner_data = {
            'name': getattr(winner_user, 'display_name', str(winner_user.id)),
            'badge_emoji': f" {get_badge_emoji(winner_stats['badge'])}" if winner_stats and winner_stats['badge'] else "",
            'xp_gain': winner_res.get('xp_gain', 0) if winner_res else 0,
            'multi_wr': winner_res.get('multi_wr') if winner_res else 0
        }
    
    badge_map = {uid: stats_map[uid]['badge'] for uid in stats_map if stats_map[uid].get('badge')}
    breakdown = await _build_breakdown_embed(bot, participant_rows, winner_data=winner_data, badge_map=badge_map)
    
    if not is_win:
        breakdown.title = "🎖️ Game Over - Rewards"
        breakdown.color = discord.Color.greyple()
    
    return embed, (breakdown if participant_rows else None), level_ups, tier_ups, winner_res, results


async def handle_game_win(bot, game, interaction, winner_user, cid, include_board: bool = True, final_time: float = None):
    """Handle a game win: award winner + participants, send breakdown embed."""
    if cid in bot.stopped_games:
        bot.stopped_games.discard(cid)
        return None, None, None, None, None, None, []

    time_taken = final_time if final_time is not None else (datetime.datetime.now() - game.start_time).total_seconds()
    embed, breakdown, level_ups, tier_ups, winner_res, results = await _process_game_results(
        bot, game, winner_user, interaction.guild.id, time_taken, include_board, is_win=True
    )
    return embed, breakdown, winner_user, winner_res, level_ups, tier_ups, results


async def handle_game_loss(bot, game, interaction, cid, include_board: bool = True):
    """Handle a game loss: award all participants based on their best greens."""
    embed, breakdown, level_ups, tier_ups, _, results = await _process_game_results(
        bot, game, None, interaction.guild.id, 999, include_board, is_win=False
    )
    return embed, breakdown, None, level_ups, tier_ups, results

async def start_multiplayer_game(bot, interaction_or_ctx, is_classic: bool, hard_mode: bool = False):
    """
    Shared logic to start a multiplayer game (Simple or Classic).
    Used by commands and 'Play Again' buttons.
    """
    # 1. Identity & Context
    is_interaction = isinstance(interaction_or_ctx, discord.Interaction)
    guild = interaction_or_ctx.guild if is_interaction else interaction_or_ctx.guild
    channel = interaction_or_ctx.channel if is_interaction else interaction_or_ctx.channel
    author = interaction_or_ctx.user if is_interaction else interaction_or_ctx.author
    cid = channel.id

    # 2. Check existence
    if cid in bot.games:
        msg = "⚠️ A game is already active in this channel! Use `/stop_game` to end it."
        if is_interaction: 
            if not interaction_or_ctx.response.is_done():
                await interaction_or_ctx.response.send_message(msg, ephemeral=True)
            else:
                await interaction_or_ctx.followup.send(msg, ephemeral=True)
        else: await interaction_or_ctx.send(msg, ephemeral=True)
        return
    if cid in bot.custom_games:
        msg = "⚠️ A custom game is already active. Use `/stop_game` first."
        if is_interaction:
            if not interaction_or_ctx.response.is_done():
                await interaction_or_ctx.response.send_message(msg, ephemeral=True)
            else:
                await interaction_or_ctx.followup.send(msg, ephemeral=True)
        else: await interaction_or_ctx.send(msg, ephemeral=True)
        return

    # Defer early to prevent timeout during DB secret selection
    if is_interaction:
        if not interaction_or_ctx.response.is_done():
            await interaction_or_ctx.response.defer()
    else:
        # For hybrid/prefix commands
        await interaction_or_ctx.defer()

    # 3. Secret Selection
    if is_classic:
        if not bot.hard_secrets:
            msg = "❌ Classic word list missing."
            if is_interaction: await interaction_or_ctx.response.send_message(msg, ephemeral=True)
            else: await interaction_or_ctx.send(msg, ephemeral=True)
            return
        secret = get_next_word_bitset(bot, guild.id, 'classic')
        
        if hard_mode:
            title = "🛡️ Wordle Started! (HARD MODE)"
            color = discord.Color.red()
            desc = "**OFFICIAL HARD RULES:**\n1. Greens must be fixed.\n2. Yellows must be reused.\n6 attempts.\n\n*Tip: Use `/help wordle` for detailed rules!*"
        else:
            title = "⚔️ Wordle Started! (Classic)"
            color = discord.Color.dark_gold()
            desc = "**Hard Mode!** 6 attempts.\n\n*Tip: Use `/help wordle` for detailed rules!*"
    else:
        if not bot.secrets:
            msg = "❌ Simple word list missing."
            if is_interaction: await interaction_or_ctx.response.send_message(msg, ephemeral=True)
            else: await interaction_or_ctx.send(msg, ephemeral=True)
            return
        secret = get_next_word_bitset(bot, guild.id, 'simple')
        title = "✨ Wordle Started! (Simple)"
        color = discord.Color.blue()
        desc = "A simple **5-letter word** has been chosen. **6 attempts** total.\n\n*Tip: Use `/help wordle` for detailed rules!*"

    # 4. Announcement - Add participation line
    embed = discord.Embed(title=title, color=color, description=desc)
    embed.add_field(name="How to Play", value="`/guess word:xxxxx` or `-g xxxxx`", inline=False)
    embed.set_footer(text="Everyone in this channel can participate.")
    
    if is_interaction:
        if not interaction_or_ctx.response.is_done():
            await interaction_or_ctx.response.send_message(embed=embed)
            msg = await interaction_or_ctx.original_response()
        else:
            # Button click (already deferred) - send to channel directly (no reply)
            msg = await channel.send(embed=embed)
    else:
        msg = await interaction_or_ctx.send(embed=embed)

    # 5. Initialize
    bot.games[cid] = WordleGame(secret, cid, author, msg.id)
    bot.games[cid].difficulty = 1 if is_classic else 0 # 0=Simple, 1=Classic
    bot.games[cid].hard_mode = hard_mode
    bot.stopped_games.discard(cid)
    # print(f"DEBUG: {'Classic ' if is_classic else ''}Game STARTED in {cid}.")
    return bot.games[cid]


class PlayAgainView(discord.ui.View):
    def __init__(self, bot, is_classic: bool, hard_mode: bool = False, is_win: bool = False):
        super().__init__(timeout=300)
        self.bot = bot
        self.is_classic = is_classic
        self.hard_mode = hard_mode
        self.is_win = is_win

        # Update button appearance based on win/loss
        if is_win:
            self.play_again.label = "Play Again"
            self.play_again.style = discord.ButtonStyle.success
            self.play_again.emoji = "🎮"
        else:
            self.play_again.label = "Try Again"
            self.play_again.style = discord.ButtonStyle.primary
            self.play_again.emoji = "🔄"

    @discord.ui.button(label="Try Again", style=discord.ButtonStyle.primary, emoji="🔄")
    async def play_again(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Defer immediately to prevent timeout
        await interaction.response.defer()
        
        # Start a new game using the same settings
        await start_multiplayer_game(self.bot, interaction, self.is_classic, self.hard_mode)
        self.stop()
        
        # Disable the button after use to prevent multiple clicks
        try:
            button.disabled = True
            await interaction.message.edit(view=self)
        except:
            pass
