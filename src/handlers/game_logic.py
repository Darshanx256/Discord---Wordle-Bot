"""
Shared game logic for handling wins, losses, and reward distribution in multiplayer games.
"""
import asyncio
import datetime
import discord
from src.game import WordleGame
from src.database import record_game_v2, get_next_secret, get_next_classic_secret
from src.utils import get_badge_emoji, get_win_flavor, get_cached_username


async def handle_game_win(bot, game, interaction, winner_user, cid):
    """
    Handle a game win: award winner + participants, send breakdown embed.
    
    Returns: (embed, breakdown_embed, winner_user, res_dict, level_ups, tier_ups)
    """
    if cid in bot.stopped_games:
        bot.stopped_games.discard(cid)
        return None, None, None, None, None, None  # Game was stopped, no reward

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
    level_ups = []  # Collect level ups for all participants
    tier_ups = []   # Collect tier ups for all participants
    
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
            # Collect level up for this participant
            if pres and pres.get('level_up'):
                level_ups.append((uid, pres['level_up']))
            # Collect tier up for this participant
            if pres and pres.get('tier_up'):
                tier_ups.append((uid, pres['tier_up']))
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

    return embed, breakdown_to_send, winner_user, res, level_ups, tier_ups


async def handle_game_loss(bot, game, interaction, cid):
    """
    Handle a game loss: award all participants based on their best greens.
    
    Returns: (embed, participant_rows_list, level_ups_list, tier_ups_list)
    """
    board_display = "\n".join([f"{h['pattern']}" for h in game.history])
    embed = discord.Embed(title="💀 GAME OVER", color=discord.Color.red())
    embed.description = f"The word was **{game.secret.upper()}**."
    embed.add_field(name="Final Board", value=board_display, inline=False)

    # Award per-player based on best guess
    participant_rows = []
    level_ups = []  # Collect level ups for all participants
    tier_ups = []   # Collect tier ups for all participants
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
            # Collect level up for this participant
            if pres and pres.get('level_up'):
                level_ups.append((uid, pres['level_up']))
            # Collect tier up
            if pres and pres.get('tier_up'):
                tier_ups.append((uid, pres['tier_up']))
        except:
            display_xp = 0
            display_wr = None
        participant_rows.append((uid, outcome_key, display_xp, display_wr))

    return embed, participant_rows, level_ups, tier_ups

async def start_multiplayer_game(bot, interaction_or_ctx, is_classic: bool):
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

    if not guild:
        msg = "❌ Command must be used in a server."
        if is_interaction: await interaction_or_ctx.response.send_message(msg, ephemeral=True)
        else: await interaction_or_ctx.send(msg, ephemeral=True)
        return

    # 2. Check existence
    if cid in bot.games:
        msg = "⚠️ A game is already active in this channel! Use `/stop_game` to end it."
        if is_interaction: await interaction_or_ctx.response.send_message(msg, ephemeral=True)
        else: await interaction_or_ctx.send(msg, ephemeral=True)
        return
    if cid in bot.custom_games:
        msg = "⚠️ A custom game is already active. Use `/stop_game` first."
        if is_interaction: await interaction_or_ctx.response.send_message(msg, ephemeral=True)
        else: await interaction_or_ctx.send(msg, ephemeral=True)
        return

    # 3. Secret Selection
    if is_classic:
        if not bot.hard_secrets:
            msg = "❌ Classic word list missing."
            if is_interaction: await interaction_or_ctx.response.send_message(msg, ephemeral=True)
            else: await interaction_or_ctx.send(msg, ephemeral=True)
            return
        secret = get_next_classic_secret(bot, guild.id)
        title = "⚔️ Wordle Started! (Classic)"
        color = discord.Color.dark_gold()
        desc = "**Hard Mode!** 6 attempts."
    else:
        if not bot.secrets:
            msg = "❌ Simple word list missing."
            if is_interaction: await interaction_or_ctx.response.send_message(msg, ephemeral=True)
            else: await interaction_or_ctx.send(msg, ephemeral=True)
            return
        secret = get_next_secret(bot, guild.id)
        title = "✨ Wordle Started! (Simple)"
        color = discord.Color.blue()
        desc = "A simple **5-letter word** has been chosen. **6 attempts** total."

    # 4. Announcement
    embed = discord.Embed(title=title, color=color, description=desc)
    embed.add_field(name="How to Play", value="`/guess word:xxxxx` or `-g xxxxx`", inline=False)
    
    if is_interaction:
        if not interaction_or_ctx.response.is_done():
            await interaction_or_ctx.response.send_message(embed=embed)
            msg = await interaction_or_ctx.original_response()
        else:
            msg = await interaction_or_ctx.followup.send(embed=embed)
    else:
        msg = await interaction_or_ctx.send(embed=embed)

    # 5. Initialize
    bot.games[cid] = WordleGame(secret, cid, author, msg.id)
    bot.games[cid].difficulty = 1 if is_classic else 0 # 0=Simple, 1=Classic
    bot.stopped_games.discard(cid)
    # print(f"DEBUG: {'Classic ' if is_classic else ''}Game STARTED in {cid}.")
    return bot.games[cid]


class PlayAgainView(discord.ui.View):
    def __init__(self, bot, is_classic: bool):
        super().__init__(timeout=300)
        self.bot = bot
        self.is_classic = is_classic

    @discord.ui.button(label="Play Again", style=discord.ButtonStyle.primary, emoji="🔄")
    async def play_again(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Start a new game using the same settings
        await start_multiplayer_game(self.bot, interaction, self.is_classic)
        self.stop()
        
        # Disable the button after use to prevent multiple clicks
        try:
            button.disabled = True
            await interaction.message.edit(view=self)
        except:
            pass
