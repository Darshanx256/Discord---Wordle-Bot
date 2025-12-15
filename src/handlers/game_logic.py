"""
Shared game logic for handling wins, losses, and reward distribution in multiplayer games.
"""
import asyncio
import datetime
import discord
from src.database import record_game_v2
from src.utils import get_badge_emoji, get_win_flavor, get_cached_username


async def handle_game_win(bot, game, interaction, winner_user, cid):
    """
    Handle a game win: award winner + participants, send breakdown embed.
    
    Returns: (embed, breakdown_embed, winner_user, res_dict)
    """
    if cid in bot.stopped_games:
        bot.stopped_games.discard(cid)
        return None, None, None, None  # Game was stopped, no reward

    time_taken = (datetime.datetime.now() - game.start_time).total_seconds()
    flavor = get_win_flavor(game.attempts_used)
    
    # Main win embed
    embed = discord.Embed(title=f"🏆 VICTORY!\n{flavor}", color=discord.Color.green())
    
    # Fetch winner badge for display
    try:
        b_res_win = bot.supabase_client.table('user_stats_v2').select('active_badge').eq('user_id', winner_user.id).execute()
        win_badge = b_res_win.data[0]['active_badge'] if b_res_win.data else None
    except:
        win_badge = None

    win_badge_str = f" {get_badge_emoji(win_badge)}" if win_badge else ""
    board_display = "\n".join([f"{h['pattern']}" for h in game.history])
    embed.description = f"**{winner_user.mention}{win_badge_str}** found **{game.secret.upper()}** in {game.attempts_used}/6!"
    embed.add_field(name="Final Board", value=board_display, inline=False)
    
    # Award winner
    res = record_game_v2(bot, winner_user.id, interaction.guild.id, 'MULTI', 'win', game.attempts_used, time_taken)
    if res:
        xp_gain = res.get('xp_gain', 0)
        embed.add_field(name="Winner Rewards", value=f"+ {xp_gain} XP | 📈 WR: {res.get('multi_wr')}", inline=False)
    
    # Award participants and collect data
    others = game.participants - {winner_user.id}
    participant_rows = []
    
    for uid in others:
        max_greens = 0
        for h in game.history:
            if getattr(h.get('user'), 'id', None) == uid:
                guess = h.get('word', '') or ''
                greens = sum(1 for a, b in zip(guess.upper(), game.secret.upper()) if a == b)
                if greens > max_greens:
                    max_greens = greens

        if max_greens >= 5:
            outcome_key = 'win'
        elif max_greens == 4:
            outcome_key = 'correct_4'
        elif max_greens == 3:
            outcome_key = 'correct_3'
        elif max_greens == 2:
            outcome_key = 'correct_2'
        elif max_greens == 1:
            outcome_key = 'correct_1'
        else:
            outcome_key = 'participation'

        pres = await asyncio.to_thread(record_game_v2, bot, uid, interaction.guild.id, 'MULTI', outcome_key, game.attempts_used, 999)
        try:
            display_xp = pres.get('xp_gain', 0) if pres else 0
            display_wr = pres.get('multi_wr') if pres else None
        except:
            display_xp = 0
            display_wr = None
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
        # Batch fetch badges
        all_uids = [uid for uid, *_ in participant_rows]
        badge_map = {}
        if all_uids:
            try:
                b_resp = bot.supabase_client.table('user_stats_v2').select('user_id, active_badge').in_('user_id', all_uids).execute()
                if b_resp.data:
                    for r in b_resp.data:
                        badge_map[r['user_id']] = r.get('active_badge')
            except:
                badge_map = {}

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

    return embed, breakdown_to_send, winner_user, res


async def handle_game_loss(bot, game, interaction, cid):
    """
    Handle a game loss: award all participants based on their best greens.
    
    Returns: (embed, participant_rows_list)
    """
    board_display = "\n".join([f"{h['pattern']}" for h in game.history])
    embed = discord.Embed(title="💀 GAME OVER", color=discord.Color.red())
    embed.description = f"The word was **{game.secret.upper()}**."
    embed.add_field(name="Final Board", value=board_display, inline=False)

    # Award per-player based on best guess
    participant_rows = []
    for uid in game.participants:
        max_greens = 0
        for h in game.history:
            if getattr(h.get('user'), 'id', None) == uid:
                guess = h.get('word', '') or ''
                greens = sum(1 for a, b in zip(guess.upper(), game.secret.upper()) if a == b)
                if greens > max_greens:
                    max_greens = greens

        if max_greens >= 5:
            outcome_key = 'win'
        elif max_greens == 4:
            outcome_key = 'correct_4'
        elif max_greens == 3:
            outcome_key = 'correct_3'
        elif max_greens == 2:
            outcome_key = 'correct_2'
        elif max_greens == 1:
            outcome_key = 'correct_1'
        else:
            outcome_key = 'participation'

        pres = await asyncio.to_thread(record_game_v2, bot, uid, interaction.guild.id, 'MULTI', outcome_key, 6, 999)
        try:
            display_xp = pres.get('xp_gain', 0) if pres else 0
            display_wr = pres.get('multi_wr') if pres else None
        except:
            display_xp = 0
            display_wr = None
        participant_rows.append((uid, outcome_key, display_xp, display_wr))

    return embed, participant_rows
