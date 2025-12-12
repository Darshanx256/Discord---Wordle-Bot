import discord
import random
from src.config import KEYBOARD_LAYOUT, TOP_GG_LINK, TIERS, C_GAMES, C_WINRATE
from src.utils import EMOJIS, get_tier_display

def get_markdown_keypad_status(used_letters: dict) -> str:
    #egg start
    extra_line = ""
    if random.randint(1,50) == 1:
        extra_line = (
            "\n\n"
            "> **🎉 RARE DUCK OF LUCK SUMMONED! 🎉**\n"
            "> 🦆 CONGRATULATIONS! You summoned a RARE Duck of Luck!\n"
            "> Have a nice day!"
        )
    #egg end

    """Generates the stylized keypad using Discord Markdown."""
    output_lines = []
    for row in KEYBOARD_LAYOUT:
        line = ""
        for char_key in row:
            char = char_key.lower()
            formatting = ""
            if char in used_letters['correct']: formatting = "correct"
            elif char in used_letters['present']: formatting = "misplaced"
            elif char in used_letters['absent']: formatting = "absent"
            else : formatting = "unknown"
            
            # --- START LINE MODIFICATION ---
            emoji_key = f"{char}_{formatting}"
            emoji_display = EMOJIS.get(emoji_key, char_key)
            line += emoji_display + " "            
            # --- END LINE MODIFICATION ---
            
        output_lines.append(line.strip())

    output_lines[1] = u"\u2007\u2007" + output_lines[1]
    output_lines[2] = u"\u2007\u2007\u2007\u2007" + output_lines[2] 
    keypad_display = "\n".join(output_lines)
        
    return keypad_display + "\n" + extra_line

class LeaderboardView(discord.ui.View):
    def __init__(self, bot, data, title, color, interaction_user):
        super().__init__(timeout=60)
        self.bot = bot
        self.data = data  
        self.title = title
        self.color = color
        self.user = interaction_user
        self.current_page = 0
        self.items_per_page = 10
        self.total_pages = max(1, (len(data) - 1) // self.items_per_page + 1)
        self.update_buttons()

    def update_buttons(self):
        self.first_page.disabled = (self.current_page == 0)
        self.prev_page.disabled = (self.current_page == 0)
        self.next_page.disabled = (self.current_page == self.total_pages - 1)
        self.last_page.disabled = (self.current_page == self.total_pages - 1)

    def create_embed(self):
        start = self.current_page * self.items_per_page
        end = start + self.items_per_page
        page_data = self.data[start:end]

        description_lines = []
        if not page_data: description_lines.append("No data available.")
        else:
            scores = [d[5] for d in self.data]
            
            for rank, name, w, g, rate, score in page_data:
                # Determine Rank Icon and Tier
                rank_index = sum(1 for s in scores if s < score)
                perc = rank_index / len(scores) if scores else 0
                tier_icon, _ = get_tier_display(perc)

                medal = {1:"🥇", 2:"🥈", 3:"🥉"}.get(rank, f"`#{rank}`")
                
                description_lines.append(f"{medal} {tier_icon} **{name}**\n   > Score: **{score:.2f}** | Wins: {w} | Games: {g}")

        embed = discord.Embed(title=self.title, description="\n".join(description_lines), color=self.color)
        embed.set_footer(text=f"Page {self.current_page + 1}/{self.total_pages} • Total Players: {len(self.data)}")
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.user:
            await interaction.response.send_message("❌ This is not your menu.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="<<", style=discord.ButtonStyle.grey)
    async def first_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = 0
        self.update_buttons()
        await interaction.response.edit_message(embed=self.create_embed(), view=self)

    @discord.ui.button(label="<", style=discord.ButtonStyle.blurple)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.create_embed(), view=self)

    @discord.ui.button(label=">", style=discord.ButtonStyle.blurple)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.create_embed(), view=self)

    @discord.ui.button(label=">>", style=discord.ButtonStyle.grey)
    async def last_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = self.total_pages - 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.create_embed(), view=self)

class HelpView(discord.ui.View):
    def __init__(self, interaction_user):
        super().__init__(timeout=120)
        self.user = interaction_user
        self.page = 1 # 1=Basic, 2=Advanced
        self.update_buttons()

    def update_buttons(self):
        if self.page == 1:
            self.btn_basic.disabled = True
            self.btn_basic.style = discord.ButtonStyle.secondary
            self.btn_advanced.disabled = False
            self.btn_advanced.style = discord.ButtonStyle.primary
        else:
            self.btn_basic.disabled = False
            self.btn_basic.style = discord.ButtonStyle.primary
            self.btn_advanced.disabled = True
            self.btn_advanced.style = discord.ButtonStyle.secondary
            
    def create_embed(self):
        if self.page == 1:
            # BASIC PAGE
            embed = discord.Embed(title="📚 Wordle Bot Guide (Basic)", color=discord.Color.blue())
            embed.description = "A global, competitive Wordle bot for Discord."
            
            embed.add_field(name="🎮 How to Play", value=(
                "1. **Start a Game**\n"
                "   `/wordle` (Simple 5-letter words)\n"
                "   `/wordle_classic` (Harder, full dictionary)\n\n"
                "2. **Make a Guess**\n"
                "   `/guess word:apple`\n\n"
                "3. **Hints**\n"
                "   🟩 Green: Correct letter, correct spot\n"
                "   🟨 Yellow: Correct letter, wrong spot\n"
                "   ⬜ Grey: Letter not in word"
            ), inline=False)
            
            embed.add_field(name="❓ Example", value="Guess: **APPLE**\n🟩⬜🟨⬜⬜\nA is correct! P is in word but wrong spot.", inline=False)
            embed.set_footer(text="Page 1/2 • Click 'Show More' for Advanced info")
            
        else:
            # ADVANCED PAGE
            embed = discord.Embed(title="🧠 Wordle Bot Guide (Advanced)", color=discord.Color.dark_purple())
            
            embed.add_field(name="📜 Full Command List", value=(
                "`/wordle` - Start Simple Game\n"
                "`/wordle_classic` - Start Hard Game\n"
                "`/guess [word]` - Submit guess\n"
                "`/board` - View pattern & keyboard\n"
                "`/leaderboard` - Server Rankings\n"
                "`/leaderboard_global` - Global Rankings\n"
                "`/profile` - View your stats\n"
                "`/stop_game` - Cancel game"
            ), inline=False)
            
            tier_text = "\n".join([f"{icon} **{name}** (Top {int((1-thresh)*100)}%)" for thresh, icon, name in TIERS])
            embed.add_field(name="🏆 Rankings", value=f"Our localized Elo-like system tiers players by percentile:\n{tier_text}", inline=False)
            
            embed.add_field(name="🧮 Score Calculation", value=(
                "Score = `(Wins + Bayesian_Constant) / (Games + Constant)`\n"
                "Winning in fewer attempts boosts your internal rating!"
            ), inline=False)
            
            embed.add_field(name="🔗 Useful Links", value=f"• [Vote on Top.gg]({TOP_GG_LINK})\n• Credits: Icon 'octopus' made by Whitevector - Flaticon", inline=False)
            
            embed.set_footer(text="Page 2/2")

        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.user:
            await interaction.response.send_message("❌ This is not your help menu.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Basic Info", style=discord.ButtonStyle.secondary)
    async def btn_basic(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.create_embed(), view=self)

    @discord.ui.button(label="Show More", style=discord.ButtonStyle.primary)
    async def btn_advanced(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = 2
        self.update_buttons()
        await interaction.response.edit_message(embed=self.create_embed(), view=self)
