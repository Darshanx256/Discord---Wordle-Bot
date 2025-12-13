import discord
import random
import datetime # Added for time calc
from discord import ui
from src.config import KEYBOARD_LAYOUT, TOP_GG_LINK, TIERS
from src.utils import EMOJIS, get_tier_display, get_win_flavor # Added utils

def get_markdown_keypad_status(used_letters: dict) -> str:
    #egg start
    extra_line = ""
    rng = random.randint(1, 100)
    if rng == 1:
        extra_line = (
            "\n> **🎉 RARE DUCK OF LUCK SUMMONED! 🎉**\n"
            "> 🦆 You summoned a RARE Duck of Luck!"
        )
    elif rng == 2:
        extra_line = "\n> *The letters are watching you...* 👁️"
    elif rng == 3:
        extra_line = "\n> *Does this keyboard feel sticky to you?* 🍬"
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

# --- SOLO MODE UI ---

class SoloGuessModal(ui.Modal, title="Enter your Guess"):
    guess_input = ui.TextInput(label="5-Letter Word", min_length=5, max_length=5, required=True)

    def __init__(self, bot, game, view_ref):
        super().__init__()
        self.bot = bot
        self.game = game
        self.view_ref = view_ref # Review to the SoloView to disable buttons if over

    async def on_submit(self, interaction: discord.Interaction):
        # Game Logic Integration
        guess = self.guess_input.value.lower().strip()
        
        # Validation
        if not guess.isalpha():
            return await interaction.response.send_message("⚠️ Letters only.", ephemeral=True)
        if guess not in self.bot.valid_set:
            return await interaction.response.send_message(f"⚠️ **{guess.upper()}** not in dictionary.", ephemeral=True)
        if self.game.is_duplicate(guess):
             return await interaction.response.send_message(f"⚠️ **{guess.upper()}** already guessed.", ephemeral=True)
            
        # Process Turn
        pat, win, game_over = self.game.process_turn(guess, interaction.user)
        
         # Progress Bar Logic
        filled = "●" * self.game.attempts_used
        empty = "○" * (6 - self.game.attempts_used)
        progress_bar = f"[{filled}{empty}]"

        # Board Display
        board_display = "\n".join([f"{h['pattern']}" for h in self.game.history])
        keypad = get_markdown_keypad_status(self.game.used_letters)
        
        # Embed Update
        if win:
            time_taken = (datetime.datetime.now() - self.game.start_time).total_seconds()
            flavor = get_win_flavor(self.game.attempts_used)
            embed = discord.Embed(title=f"🏆 VICTORY! {flavor}", color=discord.Color.green())
            embed.description = f"**{interaction.user.mention}** found **{self.game.secret.upper()}**!"
            embed.add_field(name="Final Board", value=board_display, inline=False)
            
            # Record Results
            from src.database import record_game_v2
            res = record_game_v2(self.bot, interaction.user.id, None, 'SOLO', 'win', self.game.attempts_used, time_taken)
            if res:
                xp_show = f"**{res.get('xp_gain',0)}** 💠"
                embed.add_field(name="Progression", value=f"➕ {xp_show} XP | 📈 WR: {res.get('solo_wr')}", inline=False)
                
                if res.get('level_up'):
                    embed.description += f"\n\n🔼 **LEVEL UP!** You are now **Level {res['level_up']}**! 🔼"

            embed.set_footer(text=f"Time: {time_taken:.1f}s")
            self.view_ref.disable_all() # Disable button
            
            # Clean up game
            self.bot.solo_games.pop(interaction.user.id, None)

        elif game_over:
            embed = discord.Embed(title="💀 GAME OVER", color=discord.Color.red())
            embed.description = f"The word was **{self.game.secret.upper()}**."
            embed.add_field(name="Final Board", value=board_display, inline=False)
            
            # Record Loss
            from src.database import record_game_v2
            record_game_v2(self.bot, interaction.user.id, None, 'SOLO', 'loss', 6, 999)
            
            self.view_ref.disable_all()
            self.bot.solo_games.pop(interaction.user.id, None)

        else:
            embed = discord.Embed(title=f"Solo Wordle | Attempt {self.game.attempts_used}/6", color=discord.Color.gold())
            embed.add_field(name="Board", value=board_display, inline=False)
            embed.add_field(name="Keyboard", value=keypad, inline=False)
            embed.set_footer(text=f"{6 - self.game.attempts_used} tries left {progress_bar}")

        # Update the message (Embed + View)
        await interaction.response.edit_message(embed=embed, view=self.view_ref)

class SoloView(ui.View):
    def __init__(self, bot, game, user):
        super().__init__(timeout=900) # 15 mins
        self.bot = bot
        self.game = game
        self.user = user
    
    def disable_all(self):
        for item in self.children:
            item.disabled = True
            
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.user:
            await interaction.response.send_message("Private game.", ephemeral=True)
            return False
        return True

    @ui.button(label="Enter Guess", style=discord.ButtonStyle.success, emoji="⌨️")
    async def guess_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(SoloGuessModal(self.bot, self.game, self))

# --- EXISTING VIEWS ---

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
            # Data format expected: (Rank, Name, Wins, WR, XP, TierInfo)
            # We need to adapt this based on what bot.py passes.
            # Assuming bot.py passes tuples like: (Rank, Name, Wins, XP, WR, TierIcon)
            
            for row in page_data:
                # Unpack flexibly or assuming fixed structure
                # Let's assume structure: (Rank, Name, Wins, XP, WR, TierIcon)
                rank, name, wins, xp, wr, icon = row
                
                medal = {1:"🥇", 2:"🥈", 3:"🥉"}.get(rank, f"`#{rank}`")
                
                description_lines.append(f"{medal} {icon} **{name}**\n   > WR: **{wr}** | XP: {xp} | Wins: {wins}")

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
                "   `/wordle_classic` (Harder, full dictionary)\n"
                "   `/solo` (Private Solo Mode)\n\n"
                "2. **Make a Guess**\n"
                "   `/guess word:apple` (or use buttons in Solo)\n\n"
                "3. **Hints**\n"
                "   🟩 Green: Correct letter, correct spot\n"
                "   🟨 Yellow: Correct letter, wrong spot\n"
                "   ⬜ Grey: Letter not in word"
            ), inline=False)
            
            embed.add_field(name="❓ Example", value="Guess: **APPLE**\n🟩⬜🟨⬜⬜\nA is correct! P is in word but wrong spot.", inline=False)
            embed.set_footer(text="Page 1/2 • Click 'Show More' for Advanced info")
            
        else:
            # ADVANCED PAGE
            embed = discord.Embed(title="🧠 Wordle Bot Guide (V2)", color=discord.Color.dark_purple())
            
            embed.add_field(name="📜 Full Command List", value=(
                "`/wordle` - Start Simple Game\n"
                "`/wordle_classic` - Start Hard Game\n"
                "`/solo` - Private Game\n"
                "`/guess [word]` - Submit guess\n"
                "`/leaderboard` - Server Rankings\n"
                "`/leaderboard_global` - Global Rankings\n"
                "`/profile` - View your XP & WR\n"
                "`/shop` - Buy Badge Cosmetics\n"
                "`/stop_game` - Cancel game"
            ), inline=False)
            
            tier_text = "\n".join([f"{t['icon']} **{t['name']}** (> {t['min_wr']} WR)" for t in TIERS])
            embed.add_field(name="🏆 Rankings", value=f"Our new Progression System tracks Wordle Rating (WR) and XP:\n{tier_text}", inline=False)
            
            embed.add_field(name="🧮 Scoring", value=(
                "XP based on activity. WR based on Skill + Activity.\n"
                "Faster wins = More Points!"
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
