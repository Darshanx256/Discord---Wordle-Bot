import discord
from discord import ui
from src.utils import EMOJIS, get_badge_emoji, calculate_level, get_badge_full_display

class LeaderboardViewV2(ui.View):
    """
    Component V2: Minimalist Leaderboard View.
    Modular, efficient, and designed for clean data presentation.
    """
    def __init__(self, bot, data, title, color, interaction_user, footer_text: str = None):
        super().__init__(timeout=60)
        self.bot = bot
        self.data = data
        self.title = title
        self.color = color
        self.user = interaction_user
        self.footer_text = footer_text

    def _render_chart(self, data):
        """Generates the text chart for the current page."""
        lines = []
        if not data:
            return ["No data available."]

        for row in data:
            # Flexible Unpacking: (Rank, Name, Wins, XP, WR, TierIcon, ActiveBadge)
            rank, name, wins, xp, wr, icon, badge = row
            
            # Badge Handling
            badge_emoji = get_badge_emoji(badge) if badge else ""
            badge_str = f" {badge_emoji}" if badge_emoji else ""

            # Rank Styling
            if rank == 1:   rank_str = "🥇"
            elif rank == 2: rank_str = "🥈"
            elif rank == 3: rank_str = "🥉"
            else:           rank_str = f"`#{rank}`"

            lvl = calculate_level(xp)

            # Minimalist Line
            # Format: 🥇 🛡️ **Name** 💠 Lvl 50 | 📈 1200 WR
            line = f"{rank_str} {icon} **{name}{badge_str}**\n   **{wr}** WR • Level {lvl}"
            lines.append(line)
        
        return lines

    def create_embed(self):
        chart_lines = self._render_chart(self.data)
        embed = discord.Embed(title=self.title, description="\n".join(chart_lines), color=self.color)
        footer_text = self.footer_text or f"Total: {len(self.data)} Players Displayed"
        embed.set_footer(text=footer_text)
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.user:
            await interaction.response.send_message("❌ This is not your menu.", ephemeral=True)
            return False
        return True

class ProfileViewV2(ui.View):
    """
    Component V2: Minimalist Profile View.
    Focuses on clarity, typography, and essential player data.
    """
    def __init__(self, bot, profile_data, user_obj):
        super().__init__(timeout=60)
        self.bot = bot
        self.p = profile_data
        self.user = user_obj

    def create_embed(self):
        p = self.p
        lvl = p.get('level', 1)
        tier = p.get('tier', {})
        tier_name = tier.get('name', 'Unranked')
        # Determine Badge (minimal)
        badge_prefix = ""
        if p.get('active_badge'):
            badge_full = get_badge_full_display(p['active_badge'])
            badge_prefix = f"{badge_full} • " if badge_full else ""

        embed = discord.Embed(color=discord.Color.dark_theme())
        embed.set_author(name=f"{self.user.display_name}", icon_url=self.user.display_avatar.url)
        
        # Main Header: Badge + Level + Tier
        embed.description = f"{badge_prefix}Level {lvl} • {tier_name}"

        # Statistics Section
        multi_stats = f"WR: {p['multi_wr']}\nWins: {p['multi_wins']}"
        solo_stats = f"WR: {p['solo_wr']}\nWins: {p['solo_wins']}"
        embed.add_field(name="Multiplayer", value=multi_stats, inline=True)
        embed.add_field(name="Solo Play", value=solo_stats, inline=True)

        # Collection Section
        eggs = p.get('eggs', {})
        if eggs:
            egg_lines = [f"{k.capitalize()} {v}x" for k, v in eggs.items()]
            embed.add_field(name="Collection", value="\n".join(egg_lines), inline=False)
        else:
            embed.add_field(name="Collection", value="No items found.", inline=False)

        # Progression Bar
        curr = p.get('current_level_xp', 0)
        nxt = p.get('next_level_xp', 100)
        pct = min(1.0, curr / nxt)
        bar = "█" * int(10 * pct) + "░" * (10 - int(10 * pct))
        embed.add_field(name="Progression", value=f"`{bar}` {curr}/{nxt} XP", inline=False)

        embed.set_footer(text="Wordle Game Bot")
        return embed
