"""
Shared game logic for handling wins, losses, and reward distribution in multiplayer games.
"""
import asyncio
import datetime
import discord
from src.game import WordleGame
from src.database import record_game_v2, get_next_word_bitset
from src.utils import get_badge_emoji, get_win_flavor, get_cached_username


async def handle_game_win(bot, game, interaction, winner_user, cid, include_board: bool = True):
    """
    Handle a game win: award winner + participants, send breakdown embed.
    
    Returns: (embed, breakdown_embed, winner_user, res_dict, level_ups, tier_ups, participant_results)
    """
    if cid in bot.stopped_games:
        bot.stopped_games.discard(cid)
        return None, None, None, None, None, None, []  # Game was stopped, no reward

    time_taken = (datetime.datetime.now() - game.start_time).total_seconds()
    flavor = get_win_flavor(game.attempts_used)
    
    # 1. BATCH FETCH STATS for all participants (Winner + Others)
    all_participants = list(game.participants)
    stats_map = {} # {uid: {'wr': 1200, 'badge': '...', 'daily': 50}}
    
    try:
        # Fetch WR, XP and Badges
        s_res = bot.supabase_client.table('user_stats_v2').select('user_id, multi_wr, xp, active_badge').in_('user_id', all_participants).execute()
        for r in s_res.data:
            stats_map[r['user_id']] = {'wr': r['multi_wr'], 'xp': r['xp'], 'badge': r['active_badge'], 'daily': 0}
            
        # Fetch Daily Gains (match_history table)
        today_start = datetime.datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        h_res = bot.supabase_client.table('match_history').select('user_id, wr_delta').in_('user_id', all_participants).gte('created_at', today_start.isoformat()).gt('wr_delta', 0).execute()
        for r in h_res.data:
            uid = r['user_id']
            if uid in stats_map:
                stats_map[uid]['daily'] += r['wr_delta']
    except Exception as e:
        print(f"⚠️ Batch fetch error: {e}")

    winner_stats = stats_map.get(winner_user.id, {'wr': 0, 'badge': None, 'daily': 0})
    win_badge = winner_stats['badge']
    
    # Main win/reward embed
    if include_board:
        embed = discord.Embed(title=f"🏆 VICTORY!\n{flavor}", color=discord.Color.green())
        win_badge_str = f" {get_badge_emoji(win_badge)}" if win_badge else ""
        board_display = "\n".join([f"# {h['pattern']}" for h in game.history])
        embed.description = f"**{winner_user.mention}{win_badge_str}** found **{game.secret.upper()}** in {game.attempts_used}/6!"
        embed.add_field(name="Final Board", value=board_display, inline=False)
    else:
        # Just a reward summary embed
        embed = discord.Embed(title="✨ Game Rewards", color=discord.Color.gold())
        win_badge_str = f" {get_badge_emoji(win_badge)}" if win_badge else ""
        embed.description = f"**{winner_user.mention}{win_badge_str}** won the game!"

    # Simulate winner rewards locally
    from src.database import simulate_record_game
    res = simulate_record_game(
        bot, winner_user.id, 'MULTI', 'win', 
        game.attempts_used, time_taken, 
        pre_wr=winner_stats['wr'], pre_xp=winner_stats['xp'], pre_daily=winner_stats['daily']
    )
    
    # Background DB update for winner
    asyncio.create_task(asyncio.to_thread(
        record_game_v2, bot, winner_user.id, interaction.guild.id, 'MULTI', 'win', 
        game.attempts_used, time_taken, 
        pre_wr=winner_stats['wr'], pre_daily=winner_stats['daily']
    ))

    if res:
        xp_gain = res.get('xp_gain', 0)
        embed.add_field(name="Winner Rewards", value=f"+ {xp_gain} XP | 📈 WR: {res.get('multi_wr')}", inline=False)
    
    # Add time taken to footer
    embed.set_footer(text=f"⏱️ Solved in {time_taken:.1f}s")
    
    # Award participants concurrently
    others = list(game.participants - {winner_user.id})
    participant_data = []
    
    # Pre-calculate unique greens per user
    user_unique_greens = {} # {uid: count}
    discovered_indices = set()
    
    for h in game.history:
        uid = getattr(h.get('user'), 'id', None)
        if not uid: continue
        
        guess = h.get('word', '') or ''
        # Check greens
        for i, (g_char, s_char) in enumerate(zip(guess.upper(), game.secret.upper())):
            if g_char == s_char:
                if i not in discovered_indices:
                    discovered_indices.add(i)
                    user_unique_greens[uid] = user_unique_greens.get(uid, 0) + 1

    # Pre-calculate outcome keys to parallelize
    async def process_participant(uid):
        unique_greens = user_unique_greens.get(uid, 0)

        if unique_greens >= 5: outcome_key = 'win'
        elif unique_greens == 4: outcome_key = 'correct_4'
        elif unique_greens == 3: outcome_key = 'correct_3'
        elif unique_greens == 2: outcome_key = 'correct_2'
        elif unique_greens == 1: outcome_key = 'correct_1'
        else: outcome_key = 'participation'

        p_stats = stats_map.get(uid, {'wr': 1200, 'xp': 0, 'badge': None, 'daily': 0})
        
        # Simulate local result
        pres = simulate_record_game(
            bot, uid, 'MULTI', outcome_key, 
            game.attempts_used, 999, pre_wr=p_stats['wr'], pre_xp=p_stats['xp'], pre_daily=p_stats['daily']
        )
        
        # Background DB update
        asyncio.create_task(asyncio.to_thread(
            record_game_v2, bot, uid, interaction.guild.id, 'MULTI', outcome_key, 
            game.attempts_used, 999, pre_wr=p_stats['wr'], pre_daily=p_stats['daily']
        ))
        
        return (uid, outcome_key, pres)

    # Gather local simulations
    if others:
        results = await asyncio.gather(*(process_participant(uid) for uid in others))
    else:
        results = []

    participant_rows = []
    level_ups = []
    tier_ups = []

    for uid, outcome_key, pres in results:
        display_xp = pres.get('xp_gain', 0) if pres else 0
        display_wr = pres.get('multi_wr') if pres else None
        if pres and pres.get('level_up'):
            level_ups.append((uid, pres['level_up']))
        if pres and pres.get('tier_up'):
            tier_ups.append((uid, pres['tier_up']))
        participant_rows.append((uid, outcome_key, display_xp, display_wr))

    # Build breakdown embed
    breakdown = discord.Embed(title="🎖️ Rewards Summary", color=discord.Color.blurple())
    
    win_display = getattr(winner_user, 'display_name', str(winner_user.id))
    win_badge_emoji = f" {get_badge_emoji(win_badge)}" if win_badge else ""
    breakdown.add_field(
        name="Winner",
        value=f"{win_display}{win_badge_emoji} — +{res.get('xp_gain', 0)} XP | WR: {res.get('multi_wr')}",
        inline=False
    )

    if participant_rows:
        # Use our pre-fetched stats_map for badges
        badge_map = {uid: stats_map[uid]['badge'] for uid in stats_map if stats_map[uid].get('badge')}

        # Fetch all names concurrently using cache
        name_tasks = [get_cached_username(bot, uid) for uid, *_ in participant_rows]
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

    breakdown.set_footer(text="Thanks for playing — rewards applied instantly.")

    # Only return breakdown if there are participants to show
    breakdown_to_send = breakdown if participant_rows else None

    return embed, breakdown_to_send, winner_user, res, level_ups, tier_ups, results


async def handle_game_loss(bot, game, interaction, cid, include_board: bool = True):
    """
    Handle a game loss: award all participants based on their best greens.
    
    Returns: (embed, participant_rows_list, level_ups_list, tier_ups_list)
    """
    if include_board:
        board_display = "\n".join([f"# {h['pattern']}" for h in game.history])
        embed = discord.Embed(title="💀 GAME OVER", color=discord.Color.red())
        embed.description = f"The word was **{game.secret.upper()}**."
        embed.add_field(name="Final Board", value=board_display, inline=False)
    else:
        embed = discord.Embed(title="💀 GAME OVER", color=discord.Color.red())
        embed.description = f"The word was **{game.secret.upper()}**."

    # BATCH FETCH STATS for all participants
    all_participants = list(game.participants)
    stats_map = {}
    try:
        # Fetch WR, XP and Badges
        s_res = bot.supabase_client.table('user_stats_v2').select('user_id, multi_wr, xp, active_badge').in_('user_id', all_participants).execute()
        for r in s_res.data:
            stats_map[r['user_id']] = {'wr': r['multi_wr'], 'xp': r['xp'], 'badge': r['active_badge'], 'daily': 0}
        
        today_start = datetime.datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        h_res = bot.supabase_client.table('match_history').select('user_id, wr_delta').in_('user_id', all_participants).gte('created_at', today_start.isoformat()).gt('wr_delta', 0).execute()
        for r in h_res.data:
            uid = r['user_id']
            if uid in stats_map:
                stats_map[uid]['daily'] += r['wr_delta']
    except Exception as e:
        print(f"⚠️ Batch fetch error: {e}")

    # Pre-calculate unique greens per user
    user_unique_greens = {} # {uid: count}
    discovered_indices = set()
    
    for h in game.history:
        uid = getattr(h.get('user'), 'id', None)
        if not uid: continue
        
        guess = h.get('word', '') or ''
        # Check greens
        for i, (g_char, s_char) in enumerate(zip(guess.upper(), game.secret.upper())):
            if g_char == s_char:
                if i not in discovered_indices:
                    discovered_indices.add(i)
                    user_unique_greens[uid] = user_unique_greens.get(uid, 0) + 1

    # Award per-player concurrently
    async def process_participant(uid):
        unique_greens = user_unique_greens.get(uid, 0)

        if unique_greens >= 5: outcome_key = 'win'
        elif unique_greens == 4: outcome_key = 'correct_4'
        elif unique_greens == 3: outcome_key = 'correct_3'
        elif unique_greens == 2: outcome_key = 'correct_2'
        elif unique_greens == 1: outcome_key = 'correct_1'
        else: outcome_key = 'participation'

        p_stats = stats_map.get(uid, {'wr': 1200, 'xp': 0, 'badge': None, 'daily': 0})
        
        from src.database import simulate_record_game
        # Simulate local result
        pres = simulate_record_game(
            bot, uid, 'MULTI', outcome_key, 
            6, 999, pre_wr=p_stats['wr'], pre_xp=p_stats['xp'], pre_daily=p_stats['daily']
        )
        
        # Background DB update
        asyncio.create_task(asyncio.to_thread(
            record_game_v2, bot, uid, interaction.guild.id, 'MULTI', outcome_key, 
            6, 999, pre_wr=p_stats['wr'], pre_daily=p_stats['daily']
        ))
        
        return (uid, outcome_key, pres)

    results = await asyncio.gather(*(process_participant(uid) for uid in game.participants))

    participant_rows = []
    level_ups = []
    tier_ups = []
    for uid, outcome_key, pres in results:
        display_xp = pres.get('xp_gain', 0) if pres else 0
        display_wr = pres.get('multi_wr') if pres else None
        if pres and pres.get('level_up'):
            level_ups.append((uid, pres['level_up']))
        if pres and pres.get('tier_up'):
            tier_ups.append((uid, pres['tier_up']))
        participant_rows.append((uid, outcome_key, display_xp, display_wr))

    return embed, participant_rows, level_ups, tier_ups, results

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
    def __init__(self, bot, is_classic: bool, hard_mode: bool = False):
        super().__init__(timeout=300)
        self.bot = bot
        self.is_classic = is_classic
        self.hard_mode = hard_mode

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
