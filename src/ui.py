import discord
import random
import datetime # Added for time calc
from discord import ui
from src.config import KEYBOARD_LAYOUT, TOP_GG_LINK, TIERS
from src.utils import EMOJIS, get_tier_display, get_win_flavor, get_badge_emoji # Added utils

def get_markdown_keypad_status(used_letters: dict, bot=None, user_id: int=None) -> str:
    #egg start
    extra_line = ""
    rng = random.randint(1, 100)
    if rng == 1:
        egg = 'duck'
        egg_emoji = EMOJIS.get('duck', '🦆')
        extra_line = (
            f"\n> **{egg_emoji} RARE DUCK OF LUCK SUMMONED! {egg_emoji}**\n"
            f"> You summoned a RARE Duck of Luck!"
        )
        if bot and user_id:
            from src.database import trigger_egg
            try:
                trigger_egg(bot, user_id, egg)
            except:
                pass
            
    elif rng <= 4:  
        eye_emoji = EMOJIS.get('eyes', '👁️') if 'eyes' in EMOJIS else '👁️'
        extra_line = f"\n> *The letters are watching you...* {eye_emoji}"
    elif rng <= 7:
        egg = 'candy'
        egg_emoji = EMOJIS.get('candy', '🍬')
        extra_line = f"\n> *Does this keyboard feel sticky to you?* {egg_emoji}"
        if bot and user_id:
            from src.database import trigger_egg
            try:
                trigger_egg(bot, user_id, egg)
            except:
                pass
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

    output_lines[1] = u"\u2007" + output_lines[1]
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
        try:
            from src.database import record_game_v2 # Ensure import
            from src.utils import get_win_flavor # Ensure import

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
            keypad = get_markdown_keypad_status(self.game.used_letters, self.bot, interaction.user.id)
            
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
                
                # CRITICAL FIX: Send the updated message (clear content)
                await interaction.response.edit_message(content="", embed=embed, view=self.view_ref)

            elif game_over:
                embed = discord.Embed(title="💀 GAME OVER", color=discord.Color.red())
                embed.description = f"The word was **{self.game.secret.upper()}**."
                embed.add_field(name="Final Board", value=board_display, inline=False)
                
                # Record Loss
                from src.database import record_game_v2
                record_game_v2(self.bot, interaction.user.id, None, 'SOLO', 'loss', 6, 999)
                
                self.view_ref.disable_all()
                self.bot.solo_games.pop(interaction.user.id, None)
                
                # CRITICAL FIX: Send the updated message (clear content)
                await interaction.response.edit_message(content="", embed=embed, view=self.view_ref)

            else:
                # Ongoing game - show board in embed, keyboard in content
                embed = discord.Embed(title=f"Solo Wordle | Attempt {self.game.attempts_used}/6", color=discord.Color.gold())
                embed.add_field(name="Board", value=board_display, inline=False)
                embed.set_footer(text=f"{6 - self.game.attempts_used} tries left {progress_bar}")

                # Keyboard in message content to avoid 1024 char limit
                message_content = f"**Keyboard Status:**\n{keypad}"

                # Update the message (Content + Embed + View)
                await interaction.response.edit_message(content=message_content, embed=embed, view=self.view_ref)
            
        except Exception as e:
            import traceback
            print(f"ERROR in Solo Modal: {e}")
            print(traceback.format_exc())
            
            # Try to respond to the interaction if not already responded
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(f"❌ An error occurred: {str(e)}", ephemeral=True)
                else:
                    await interaction.followup.send(f"❌ An error occurred: {str(e)}", ephemeral=True)
            except:
                pass

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

    @ui.button(label="End Game", style=discord.ButtonStyle.danger, emoji="🛑")
    async def end_game_button(self, interaction: discord.Interaction, button: ui.Button):
        # End the game manually
        if interaction.user.id in self.bot.solo_games:
            self.bot.solo_games.pop(interaction.user.id, None)
            
        self.disable_all()
        await interaction.response.edit_message(content=f"⛔ **Game Ended by User.**\nThe word was **{self.game.secret.upper()}**.", view=self, embed=None)

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
                # Updated Structure: (Rank, Name, Wins, XP, WR, TierIcon, ActiveBadge)
                rank, name, wins, xp, wr, icon, badge = row
                
                # Show badge if exists (Suffix Style)
                from src.utils import get_badge_emoji
                badge_emoji = get_badge_emoji(badge) if badge else ""
                badge_str = f" {badge_emoji}" if badge_emoji else ""
                
                medal = {1:"🥇", 2:"🥈", 3:"🥉"}.get(rank, f"`#{rank}`")
                
                description_lines.append(f"{medal} {icon} **{name}{badge_str}**\n   > WR: **{wr}** | Wins: {wins}")

        embed = discord.Embed(title=self.title, description="\n".join(description_lines), color=self.color)
        embed.set_footer(text=f"Page {self.current_page + 1}/{self.total_pages} • Total Players: {len(self.data)} | Names updated every 2 days")
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
            embed.description = "A global, competitive Wordle bot for Discord with XP, ranking tiers, and Easter eggs!"
            
            embed.add_field(name="🎮 How to Play", value=(
                "1. **Start a Game**\n"
                "   `/wordle` (Simple 5-letter words)\n"
                "   `/wordle_classic` (Harder, full dictionary)\n"
                "   `/solo` (Private Solo Mode)\n\n"
                "2. **Make a Guess**\n"
                "   `/guess word:apple` or `-g apple`\n\n"
                "3. **Hints**\n"
                "   🟩 Green: Correct letter, correct spot\n"
                "   🟨 Yellow: Correct letter, wrong spot\n"
                "   ⬜ Grey: Letter not in word"
            ), inline=False)
            
            # Build example with custom emojis
            apple_example = "Guess: **APPLE**\n"
            apple_example += f"{EMOJIS.get('block_a_green', '🟩')}{EMOJIS.get('block_p_yellow', '🟨')}{EMOJIS.get('block_p_yellow', '🟨')}{EMOJIS.get('block_l_white', '⬜')}{EMOJIS.get('block_e_white', '⬜')}\n"
            apple_example += "**A** is correct! **P** is in word but wrong spot."
            
            embed.add_field(name="❓ Example", value=apple_example, inline=False)
            embed.set_footer(text="Page 1/2 • Click 'Show More' for Advanced info & Easter Eggs")
            
        else:
            # ADVANCED PAGE - Improved Layout
            embed = discord.Embed(title="🧠 Wordle Bot Guide (Advanced)", color=discord.Color.dark_purple())
            embed.description = "Deep dive into commands, tiers, and collectibles!"
            
            # Commands Section - Two columns for better organization
            embed.add_field(name="🎮 Game Commands", value=(
                "`/wordle` — Simple Game\n"
                "`/wordle_classic` — Hard Game\n"
                "`/solo` — Private Game\n"
                "`/custom` — Set Custom Word\n"
                "`/stop_game` — Cancel Game"
            ), inline=True)
            
            embed.add_field(name="📊 Stats & Profile", value=(
                "`/guess` or `-g` — Guess\n"
                "`/profile` — Your Stats\n"
                "`/leaderboard` — Server Ranks\n"
                "`/leaderboard_global` — Global\n"
                "`/shop` — Equip Badges"
            ), inline=True)
            
            # Tiers Section
            tier_text = "\n".join([
                f"{EMOJIS.get(t['icon'], t['icon'])} **{t['name']}** — WR ≥ {t['min_wr']}" 
                for t in TIERS
            ])
            embed.add_field(name="🏆 Ranking Tiers", value=tier_text, inline=False)
            
            # Easter Eggs Section
            duck_emoji = EMOJIS.get("duck", "🦆")
            dragon_emoji = EMOJIS.get("dragon", "🐲")
            candy_emoji = EMOJIS.get("candy", "🍬")
            
            embed.add_field(name="🎁 Easter Eggs & Badges", value=(
                f"**Rare Drops during `/guess`:**\n"
                f"{duck_emoji} **Duck** — Simple Mode (1/100)\n"
                f"{dragon_emoji} **Dragon** — Classic Mode (1/1000)\n"
                f"{candy_emoji} **Candy** — Both Modes (1/100)\n\n"
                "View your collection via `/profile`\n"
                "Unlock **Badges** in `/shop`!"
            ), inline=False)
            
            # Pro Tips
            embed.add_field(name="💡 Pro Tips", value=(
                "• Start with vowel-heavy words (AUDIO, RAISE)\n"
                "• Speed matters — faster solves = bonus rewards\n"
                "• Higher tiers receive scaled rewards\n"
                "• Participate in Multiplayer for extra XP"
            ), inline=False)
            
            embed.set_footer(text="Page 2/2 • Climb the global leaderboard!")

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
