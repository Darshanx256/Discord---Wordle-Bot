import discord
import random
import datetime # Added for time calc
from discord import ui
from src.config import KEYBOARD_LAYOUT, TOP_GG_LINK, TIERS
from src.utils import EMOJIS, get_win_flavor, get_badge_emoji, send_smart_message

def get_markdown_keypad_status(used_letters: dict, bot=None, user_id: int=None, blind_mode=False) -> str:
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
            
    elif rng <= 2:  
        eye_emoji = EMOJIS.get('eyes', '👁️') if 'eyes' in EMOJIS else '👁️'
        extra_line = f"\n> *The letters are watching you...* {eye_emoji}"
    elif rng <= 3:
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
            elif char in used_letters['present']: 
                if blind_mode == 'green': 
                    formatting = "unknown"
                else: 
                    formatting = "misplaced"
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
            if guess not in self.bot.all_valid_5:
                return await interaction.response.send_message(f"⚠️ **{guess.upper()}** not in dictionary.", ephemeral=True)
            if self.game.is_duplicate(guess):
                 return await interaction.response.send_message(f"⚠️ **{guess.upper()}** already guessed.", ephemeral=True)
                
            # Process Turn
            pat, win, game_over = self.game.process_turn(guess, interaction.user)
            
             # Progress Bar Logic
            filled = "●" * self.game.attempts_used
            empty = "○" * (self.game.max_attempts - self.game.attempts_used)
            progress_bar = f"[{filled}{empty}]"

            # Board Display
            board_display = "\n".join([f"{h['pattern']}" for h in self.game.history])
            
            # Embed Update
            if win:
                keypad = get_markdown_keypad_status(self.game.used_letters, self.bot, interaction.user.id, blind_mode=False)
                time_taken = (datetime.datetime.now() - self.game.start_time).total_seconds()
                flavor = get_win_flavor(self.game.attempts_used)
                embed = discord.Embed(title=f"🏆 VICTORY! {flavor}", color=discord.Color.green())
                embed.description = f"**{interaction.user.mention}** found **{self.game.secret.upper()}**!\n\n**Final Board:**\n{board_display}\n\n**Keyboard:**\n{keypad}"
                
                # Record Results
                from src.database import record_game_v2
                res = record_game_v2(self.bot, interaction.user.id, None, 'SOLO', 'win', self.game.attempts_used, time_taken)
                if res:
                    xp_show = f"**{res.get('xp_gain',0)}** 💠"
                    embed.add_field(name="Progression", value=f"➕ {xp_show} XP | 📈 WR: {res.get('solo_wr')}", inline=False)
                    
                    if res.get('level_up'):
                        embed.description += f"\n\n🔼 **LEVEL UP!** You are now **Level {res['level_up']}**! 🔼"

                embed.set_footer(text=f"Time: {time_taken:.1f}s")
                self.view_ref.disable_all()
                
                self.bot.solo_games.pop(interaction.user.id, None)
                await interaction.response.edit_message(content="", embed=embed, view=self.view_ref)

                # Streak notifications (Delayed & Ephemeral)
                if res:
                    import asyncio
                    if res.get('streak_msg'):
                        asyncio.create_task(send_smart_message(interaction, res['streak_msg'], ephemeral=True))
                    if res.get('streak_badge'):
                        asyncio.create_task(send_smart_message(interaction, f"💎 **BADGE UNLOCKED:** {get_badge_emoji(res['streak_badge'])} Badge!", ephemeral=True))

            elif game_over:
                keypad = get_markdown_keypad_status(self.game.used_letters, self.bot, interaction.user.id, blind_mode=False)
                embed = discord.Embed(title="💀 GAME OVER", color=discord.Color.red())
                embed.description = f"The word was **{self.game.secret.upper()}**.\n\n**Final Board:**\n{board_display}\n\n**Keyboard:**\n{keypad}"
                
                self.view_ref.disable_all()
                self.bot.solo_games.pop(interaction.user.id, None)
                await interaction.response.edit_message(content="", embed=embed, view=self.view_ref)

                # Streak notifications for Loss (Delayed & Ephemeral)
                try:
                    res = record_game_v2(self.bot, interaction.user.id, None, 'SOLO', 'loss', self.game.max_attempts, 999)
                    if res and res.get('streak_msg'):
                        asyncio.create_task(send_smart_message(interaction, res['streak_msg'], ephemeral=True))
                except:
                    pass

            else:
                # Ongoing game - board + keyboard in embed description
                keypad = get_markdown_keypad_status(self.game.used_letters, self.bot, interaction.user.id, blind_mode=self.game.blind_mode)
                embed = discord.Embed(title=f"Solo Wordle | Attempt {self.game.attempts_used}/{self.game.max_attempts}", color=discord.Color.gold())
                embed.description = f"**Board:**\n{board_display}\n\n**Keyboard:**\n{keypad}"
                embed.set_footer(text=f"{self.game.max_attempts - self.game.attempts_used} tries left {progress_bar}")
                await interaction.response.edit_message(content="", embed=embed, view=self.view_ref)
            
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
    def __init__(self, bot, data, title, color, interaction_user, total_count=None):
        super().__init__(timeout=60)
        self.bot = bot
        self.data = data  
        self.title = title
        self.color = color
        self.user = interaction_user
        self.current_page = 0
        self.items_per_page = 10
        self.total_pages = max(1, (len(data) - 1) // self.items_per_page + 1)
        self.total_count = total_count if total_count is not None else len(data)
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
        embed.set_footer(text=f"Page {self.current_page + 1}/{self.total_pages} • Total Players: {self.total_count} | Name changes take up to 48 hours to reflect")
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
    def __init__(self, interaction_user, initial_feature=None):
        super().__init__(timeout=120)
        self.user = interaction_user
        self.page = 0 if initial_feature else 1 # 0=Feature, 1=Basic, 2=Advanced
        self.feature = initial_feature
        self.update_buttons()

    def update_buttons(self):
        if self.page == 0:
            self.btn_basic.disabled = False
            self.btn_basic.style = discord.ButtonStyle.primary
            self.btn_advanced.disabled = False
            self.btn_advanced.style = discord.ButtonStyle.primary
        elif self.page == 1:
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
        if self.page == 0:
            return self.get_feature_help_embed(self.feature)
        if self.page == 1:
            # BASIC PAGE
            embed = discord.Embed(title=f"📚 Wordle Bot Guide (Basic)", color=discord.Color.blue())
            embed.description = "A fun and engaging Wordle bot for Discord with various different game modes, level-up system and leaderboards!"
            
            embed.add_field(name="🎮 How to Play", value=(
                "**1. Start a Game**\n"
                "• `/wordle` -> Simple 5-letter words\n"
                "• `/wordle_classic` -> Harder, full dictionary\n"
                "• `/word_rush` -> ⚡ Rapid constraint puzzles\n"
                "• `/custom` -> Custom word in channel\n"
                "• `/solo` -> Private Solo Mode\n\n"
                "**2. Make a Guess**\n"
                "• `/guess word:apple` or `-g apple`\n\n"
                "**3. Hints**\n"
                "🟩 Correct letter, correct spot\n"
                "🟨 Correct letter, wrong spot\n"
                "⬜ Letter not in word"
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
                "`/wordle` -> Simple Game\n"
                "`/wordle_classic` -> Hard Game\n"
                "`/word_rush` -> ⚡ Rush Mode\n"
                "`/solo` -> Private Game\n"
                "`/custom` -> Set Custom Word\n"
                "`/guess` or `-g` -> Guess\n"
                "`/stop_game` or `/stop_rush` -> Stop\n"
                "`/race` -> Start Race Mode"
            ), inline=True)
            
            embed.add_field(name="📊 Stats & Profile", value=(
                "`/profile` -> Your Stats\n"
                "`/leaderboard` -> Server Ranks\n"
                "`/leaderboard_global` -> Global\n"
                "`/shop` -> Equip Badges\n"
                "`/showrace` -> Resume Race\n\n"
                "🔥 **Streaks**\n"
                "Play daily to build your streak for\n"
                "Multipliers & exclusive Badges!\n"
                "*Streaks reset daily at 00:00 UTC.*"
            ), inline=True)
            
            # Tiers Section
            tier_text = "\n".join([
                f"{EMOJIS.get(t['icon'], t['icon'])} **{t['name']}** -> WR ≥ {t['min_wr']}" 
                for t in TIERS
            ])
            embed.add_field(name="🏆 Ranking Tiers", value=tier_text, inline=False)
            
            # Easter Eggs Section
            duck_emoji = EMOJIS.get("duck", "🦆")
            dragon_emoji = EMOJIS.get("dragon", "🐲")
            candy_emoji = EMOJIS.get("candy", "🍬")
            
            embed.add_field(name="🎁 Easter Eggs & Badges", value=(
                f"**Rare Drops during `/guess`:**\n"
                f"{duck_emoji} **Duck** -> Simple Mode (1/100)\n"
                f"{dragon_emoji} **Dragon** -> Classic Mode (1/1000)\n"
                f"{candy_emoji} **Candy** -> Both Modes (1/100)\n\n"
                "View your collection via `/profile`\n"
                "Unlock **Badges** in `/shop`!"
            ), inline=False)
            
            # Pro Tips
            embed.add_field(name="💡 Pro Tips", value=(
                "• Start with vowel-heavy words (AUDIO, RAISE)\n"
                "• Speed matters -> faster solves = bonus rewards\n"
                "• Higher tiers receive scaled rewards\n"
                "• Participate in Multiplayer for extra XP\n"
                "• Word Rush Checkpoints convert Points to WR!"
            ), inline=False)

            # Custom Game Options
            embed.add_field(name="🧂 Custom Game Extra Options", value=(
                "Use in `Extra options` field:\n"
                "`dict:word1,word2` -> Add custom words\n"
                "`strict_dict:list` -> ONLY use these words\n"
                "`time:0.5` -> Time limit (min, e.g. 0.5=30s)\n"
                "`player:@u1,@u2` -> Allow multiple users\n"
                "`blind:green` -> Show greens only 🟢\n"
                "`start:w1,w2` -> Force start guesses\n"
                "`title:My Text` -> Set custom title"
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

    def get_feature_help_embed(self, feature: str):
        """Generates deep-dive help for specific features."""
        if feature == "wordle":
            embed = discord.Embed(title="🟩 Wordle: Classic & Simple", color=discord.Color.green())
            
            # Build color guide with actual emoji rendering
            green_block = EMOJIS.get('block_a_green', '🟩')
            yellow_block = EMOJIS.get('block_a_yellow', '🟡')
            gray_block = EMOJIS.get('block_a_white', '⬜')
            
            # Build example game blocks
            round1 = f"{gray_block}{yellow_block}{gray_block}{green_block}{gray_block}"  # CRATE (R yellow, T green)
            round2 = f"{yellow_block}{gray_block}{yellow_block}{green_block}{gray_block}"  # SHIRK (S yellow, I yellow, T green)
            round3 = f"{gray_block}{yellow_block}{gray_block}{green_block}{yellow_block}"  # MISTS (I yellow, T green, S yellow)
            round4 = f"{green_block}{gray_block}{yellow_block}{green_block}{gray_block}"  # SPITE (S green, I yellow, T green)
            round5 = f"{green_block}{yellow_block}{gray_block}{green_block}{green_block}"  # SHIFT (S green, H yellow, T green, correct positions)
            round6 = f"{green_block}{green_block}{green_block}{green_block}{green_block}"  # STING (All correct!)
            
            embed.description = (
                "The classic game of deduction. Guess the hidden 5-letter word in 6 tries.\n\n"
                "**How to Play:**\n"
                "• Use `/guess word:xxxxx` or `-g xxxxx` to submit guesses.\n"
                "• Use `/stop_game` to end the game early.\n"
                f"• {green_block} = Correct letter in correct position\n"
                f"• {yellow_block} = Correct letter in wrong position\n"
                f"• {gray_block} = Letter not in word\n\n"
                "**Example Game:** (Secret word: **STING**)\n"
                f"1. CRATE {round1}\n"
                f"   → R is in word (wrong spot), T is correct!\n\n"
                f"2. SHIRK {round2}\n"
                f"   → S is in word, I is in word, T still correct!\n\n"
                f"3. MISTS {round3}\n"
                f"   → Getting closer! S and I need to move.\n\n"
                f"4. SPITE {round4}\n"
                f"   → S in right spot! I still wrong position.\n\n"
                f"5. SHIFT {round5}\n"
                f"   → S, T positions locked! Missing one letter.\n\n"
                f"6. STING {round6}\n"
                f"   → 🎉 SUCCESS! Solved in 6/6 tries!\n\n"
                "**Game Modes:**\n"
                "• `/wordle` - Curated 'common' words (~2.3k). Easier for beginners.\n"
                "• `/wordle_classic` - Full dictionary (~14k words). The true test.\n"
                "• `/hard_mode` - Forces you to use revealed hints in next guesses.\n\n"
                "**Rewards:**\n"
                "• **XP** is awarded for every game based on performance.\n"
                "• **WR** (Wordle Rating) increases with wins and decreases with losses.\n"
                "• Faster solves = Speed Bonus! ⚡\n"
                "• Tier multipliers boost rewards as you climb ranks.\n\n"
                "**Game Interface:**\n"
                "• **Progress Bar:** `[●●●○○○]` shows attempts used vs remaining\n"
                "• **Board Display:** See all your previous guesses with color feedback\n"
                "• **Keyboard Status:** Visual guide of used letters (updates each guess)\n"
                "• **Attempt Counter:** Always know how many tries you have left\n\n"
                "**Tips:**\n"
                "• Start with words containing common vowels (A, E, I, O, U).\n"
                "• Use process of elimination to narrow down possibilities.\n"
                "• In Hard Mode, you **must** reuse green/yellow letters in subsequent guesses!"
            )
        
        elif feature == "word_rush":
            embed = discord.Embed(title="⚡ Word Rush (Fast-Paced Mode)", color=discord.Color.brand_red())
            embed.description = (
                "A rapid-fire multiplayer game against the clock and other players!\n\n"
                "**How It Works:**\n"
                f"• Each round presents a linguistic constraint (e.g., word pattern {EMOJIS.get('block_s_green', 'S')}{EMOJIS.get('unknown', '-')}{EMOJIS.get('unknown', '-')}{EMOJIS.get('unknown', '-')}{EMOJIS.get('block_t_green', 'T')} or \"contains double L\").\n"
                "• Type valid words matching the constraint as fast as possible.\n"
                "• Watch the **traffic lights** 🟢🟡🔴 for timing guidance.\n"
                "• **Base forms only** (e.g., `APPLE` ✅, `APPLES` ❌).\n"
                "• No word reuse within the same session.\n\n"
                "**Scoring System:**\n"
                "• **1st place:** 5 Rush Points\n"
                "• **2nd place:** 4 Rush Points\n"
                "• **3rd place:** 3 Rush Points\n"
                "• **4th place:** 2 Rush Points\n"
                "• **Others:** 1 Rush Point\n\n"
                "**Special Features:**\n"
                "• 🎁 **Bonus Rounds:** Random rounds with **3x Rush Points** (e.g., longest word, most words)!\n"
                "• 🏁 **Checkpoints:** Every 12 rounds, Rush Points convert to permanent **WR** with streak multipliers applied.\n"
                "• 🔥 **Streaks:** Consecutive correct answers tracked for session stats.\n"
                "• 📊 **Stats:** Fastest reflexes and best streaks displayed at checkpoints.\n\n"
                "**Finally:**\n"
                "• Complete all **100 rounds** to become Rush Champion!\n"
                "• Game ends if 5 consecutive rounds pass without correct guesses.\n\n"
                "*Use `/word_rush` to start a session and `/stop_rush` to end early.*"
            )
        
        elif feature == "race":
            embed = discord.Embed(title="🏁 Race Mode: Competitive Challenge", color=discord.Color.gold())
            
            # Build color guide with actual emoji rendering
            green_block = EMOJIS.get('block_a_green', '🟩')
            yellow_block = EMOJIS.get('block_a_yellow', '🟡')
            gray_block = EMOJIS.get('block_a_white', '⬜')
            
            embed.description = (
                "Compete head-to-head against other players on the **same secret word**!\n\n"
                "**How to Play:**\n"
                "• Host creates a race lobby with `/race_mode`.\n"
                "• Players join the lobby (2+ required to start).\n"
                "• Everyone gets the **same word** and **6 attempts** to solve it.\n"
                "• Click **\"Enter Guess\"** button to open a popup modal.\n"
                "• Type your 5-letter guess and submit.\n"
                "• Race timer: **10 minutes** (configurable by host).\n"
                "• Use `End Game` button to forfeit your individual game.\n\n"
                "**Ranking System:**\n"
                "**Winners (Solved the word):**\n"
                "• Ranked by **speed** (fastest time wins)\n"
                "• 🥇 1st place gets highest rewards + 10% bonus!\n"
                "• 🥈 2nd, 🥉 3rd places get decreasing bonuses\n\n"
                "**Non-Winners (Failed to solve):**\n"
                f"• Ranked by **{green_block} green letters found** (more greens = better rank)\n"
                "• If tied on greens, **faster time** breaks the tie\n"
                "• Still earn XP/WR, just reduced amounts\n\n"
                "**Rewards Distribution:**\n"
                "• **Delayed Calculation:** Rewards calculated after race ends (all finish or time expires)\n"
                "• **Top ranks** get multiplier bonuses\n"
                "• **Lower ranks** receive reduced payouts but still gain progress\n"
                "• **Tier multipliers** still apply to all rewards\n"
                "• **Speed bonus** for fast solves\n\n"
                "**Game Interface:**\n"
                "• **Progress Bar:** `[●●●○○○]` shows attempts used vs remaining\n"
                "• **Board Display:** See all your previous guesses with color feedback\n"
                "• **Keyboard Status:** Visual guide of used letters (updates each guess)\n"
                "• **Attempt Counter:** Always know how many tries you have left\n\n"
                "**Race Outcomes:**\n"
                "• **Victory:** First to solve wins the race\n"
                "• **Time Limit:** Race ends after 10 minutes\n"
                "• **All Completed:** Race ends when all participants finish\n"
                "• Final leaderboard shows everyone's rank, time, and rewards\n\n"
                "*Start a race with `/race_mode` and invite friends for maximum competition!*"
            )
        
        elif feature == "solo":
            embed = discord.Embed(title="👤 Solo Mode: (Private)", color=discord.Color.blurple())
            
            # Build color guide with actual emoji rendering
            green_block = EMOJIS.get('block_a_green', '🟩')
            yellow_block = EMOJIS.get('block_a_yellow', '🟨')
            gray_block = EMOJIS.get('block_a_white', '⬜')
            
            embed.description = (
                "Practice Wordle privately without cluttering the server chat!\n\n"
                "**How to Play:**\n"
                "• Start with `/solo_mode` to create a private game.\n"
                "• Click **\"Enter Guess\"** button to open a popup modal.\n"
                "• Type your 5-letter guess and submit.\n"
                "• Only **you** can see your game board and progress.\n"
                "• Use **\"End Game\"** button to forfeit early.\n\n"
                "**Key Features:**\n"
                "• 🔒 **Completely Private:** Game board, guesses, and keyboard only visible to you\n"
                "• 💾 **Persistent:** Leave and return anytime with `/show_solo`\n"
                "• 📊 **Full Progression:** Earn XP, WR, and maintain daily streaks\n"
                "• ⏱️ **Speed Tracking:** Faster solves earn speed bonuses\n"
                "• 🔥 **Streak Notifications:** Get ephemeral alerts for streak milestones\n"
                "• 💎 **Badge Unlocks:** Unlock badges just like server games\n"
                "• ⌨️ **Live Keyboard:** See which letters you've used with color coding:\n"
                f"  {green_block} Correct position | {yellow_block} Wrong position | {gray_block} Not in word\n\n"
                "**Game Interface:**\n"
                "• **Progress Bar:** `[●●●○○○]` shows attempts used vs remaining\n"
                "• **Board Display:** See all your previous guesses with color feedback\n"
                "• **Keyboard Status:** Visual guide of used letters (updates each guess)\n"
                "• **Attempt Counter:** Always know how many tries you have left\n\n"
                "**Victory & Defeat:**\n"
                "• **Win:** Solve within 6 tries → Earn XP/WR based on attempts & speed\n"
                "• **Loss:** Run out of tries → Word revealed, streaks may break\n"
                "• **Rewards:** Calculated instantly with tier multipliers applied\n"
                "• **Flavor Text:** Get unique victory messages based on solve speed!\n\n"
                "**Commands:**\n"
                "• `/solo_mode` - Start a new private game\n"
                "• `/show_solo` - Resume your active game if you navigated away\n"
                "• **Enter Guess** button - Submit guesses via popup modal\n"
                "• **End Game** button - Forfeit current game\n\n"
                "**Why Solo Mode?**\n"
                "• Perfect for practicing without pressure\n"
                "• Great for maintaining streaks during quiet hours\n"
                "• Ideal for testing strategies before competitive modes\n"
                "• No chat spam - keeps server channels clean\n\n"
                "*Solo Mode offers the full Wordle experience in a private, distraction-free environment!*"
            )
        
        elif feature == "custom":
            embed = discord.Embed(title="🧂 Custom Mode: Create Your Own Challenge", color=discord.Color.teal())
            
            embed.description = (
                "Design personalized Wordle games with complete control over rules and settings!\n\n"
                "**How to Start:**\n"
                "• Use `/custom_mode` to open the setup interface\n"
                "• Click **\"Set Up\"** button to open the configuration modal\n"
                "• Fill in your custom parameters (word, tries, options)\n"
                "• Game launches immediately in the channel\n\n"
                "**Basic Settings:**\n"
                "• **Word:** Your secret 5-letter word (any alphabetic word)\n"
                "• **Tries:** Number of attempts (3-10, default: 6)\n"
                "• **Reveal on Loss:** Show word when game ends (yes/no, default: yes)\n"
                "• **Keyboard:** Display letter status guide (yes/no, default: yes)\n\n"
                "**Advanced Options:** *(Enter in \"Extra options\" field)*\n\n"
                "**📚 Custom Dictionary:**\n"
                "• `dict:apple,grape,stone` - Add valid guess words (up to 1000)\n"
                "• `strict_dict:apple,grape` - ONLY allow guesses from your list (game becomes difficult)\n"
                "• Perfect for themed games or fun exercises\n\n"
                "**⏱️ Time Limits:**\n"
                "• `time:10` - Set countdown timer in minutes (0.5-360)\n"
                "• `time:0.5` - Ultra-fast 30-second challenge!\n"
                "• Game ends when timer expires\n\n"
                "**👥 Player Restrictions:**\n"
                "• `player:@username` - Restrict to specific user\n"
                "• `player:@alice,@bob,123456789` - Allow multiple players (max 20)\n"
                "• Supports mentions, user IDs, or @username format\n"
                "• Players must be present in the channel\n\n"
                "**🙈 Blind Mode:**\n"
                "• `blind:yes` or `blind:full` - Hides the BOARD plus COLOR feedback, use keyboard colors to solve (hardcore mode!)\n"
                "• `blind:green` - Only show green letters, hide yellow/gray\n"
                "• Tests pure memory and deduction skills\n\n"
                "**🎯 Starting Words:**\n"
                "• `start:crane` - Pre-fill first guess (word revealed to all)\n"
                "• `start:crane,slate` - Multiple starting words (max 10)\n"
                "• Great for testing or creating puzzles\n"
                "• If number of start words exceed number of tries, one try is given\n"
                "• Starting words cannot be the answer\n\n"
                "**🏷️ Custom Title:**\n"
                "• `title:Friday Challenge` - Set custom game name\n"
                "• `title:Boss Battle Mode` - Add personality to your game\n"
                "• Max 100 characters, no mentions/links\n\n"
                "**Example Configurations:**\n"
                "```\n"
                "Easy Tutorial:\n"
                "Word: HEART | Tries: 8 | start:crane\n"
                "\n"
                "Speed Challenge:\n"
                "Word: BLAZE | Tries: 6 | time:1 | title:60 Second Rush\n"
                "\n"
                "Themed Puzzle:\n"
                "Word: BEACH | dict:ocean,waves,shore,sandy\n"
                "\n"
                "Hardcore Mode:\n"
                "Word: QUIRK | Tries: 4 | blind:yes | strict_dict:quirk,quark,quick\n"
                "\n"
                "Private Duel:\n"
                "Word: START | player:@player1,@player2 | time:5\n"
                "```\n\n"
                "**How to Play Custom Games:**\n"
                "• Use `/guess word:xxxxx` or `-g xxxxx` like normal\n"
                "• Restricted games only accept guesses from allowed players\n"
                "• Custom dictionary limits which words are valid\n"
                "• Use `/stop_game` to end early\n\n"
                "**Tips for Hosts:**\n"
                "• Test your custom dictionary first - make sure the answer is guessable!\n"
                "• Strict dictionaries should include common starter words (CRANE, SLATE, etc.)\n"
                "• Blind mode is VERY difficult - consider adding extra tries\n"
                "• Time limits add pressure - balance difficulty accordingly\n"
                "• Starting words can teach specific strategies or letter patterns\n\n"
                "**Rewards:**\n"
                "• Custom games **do NOT award XP or WR** (to prevent farming)\n"
                "• Perfect for practice, challenges, or pure fun\n"
                "• Great for teaching new players or testing strategies\n\n"
                "*Custom Mode lets you craft unique Wordle experiences for any occasion!*"
            )
        
        elif feature == "progression":
            embed = discord.Embed(title="📈 Progression & Tiers", color=discord.Color.purple())
            
            # Build streak badge emojis properly
            spark_badge = EMOJIS.get('7_streak', '✨')
            ember_badge = EMOJIS.get('14_streak', '🔥')
            mythic_badge = EMOJIS.get('28_streak', '🔵')
            
            embed.description = (
                "Climb the ranks from Challenger beginner to Legendary Master!\n\n"
                "**Core Stats Explained:**\n"
                "• **XP (Experience Points)** - Earned every game, determines your **Level**\n"
                "• **WR (Wordle Rating)** - Win/loss-based score, determines your **Tier**\n"
                "• **Solo WR** - Separate rating for Solo Mode games\n"
                "• **Multi WR** - Rating for multiplayer games (Wordle, Race, Rush)\n\n"
                "**How Rewards Work:**\n"
                "• Base XP/WR earned per game varies by mode and performance\n"
                "• **Tier Multipliers** boost ALL rewards as you climb ranks\n"
                "• **Speed Bonus** for fast solves (under 60 seconds)\n"
                "• **Anti-Grind Protection** reduces gains after many daily games (resets daily)\n"
                "• **Attempt Bonus** - Solving in fewer tries earns more rewards\n\n"
                "**Daily Streaks System:**\n"
                "Play at least one game every day to build your streak!\n\n"
                "**Streak Milestones:**\n"
                f"• **3 Days:** 2x WR Multiplier ⚡\n"
                f"• **7 Days:** {spark_badge} **'First Spark'** Badge Unlocked\n"
                f"• **10 Days:** 2.5x WR Multiplier 🔥\n"
                f"• **14 Days:** {ember_badge} **'Ember'** Badge Unlocked\n"
                f"• **28 Days:** {mythic_badge} **'Mythic'** Badge Unlocked\n"
                f"• **35 Days:** 3x WR Multiplier 👑\n\n"
                "**Streak Rules:**\n"
                "• Maintained by playing **any mode** (Solo, Wordle, Race, Rush)\n"
                "• **Wins AND Losses count** - just play daily!\n"
                "• Streak breaks if you miss a full day (24-hour window)\n"
                "• Multipliers stack with tier bonuses for massive rewards\n"
                "• Badges display on your profile permanently\n\n"
                "**Leveling System:**\n"
                "• XP required increases per level (scaling formula)\n"
                "• Higher levels unlock bragging rights and prestige\n"
                "• No level cap - climb as high as you can!\n"
                "• Check your progress with `/profile`\n\n"
                "**WR Rating System:**\n"
                "• **Wins:** +WR (amount based on tier, mode, speed)\n"
                "• **Losses:** -WR (reduced penalty at higher tiers)\n"
                "• **Race Mode:** Rank-based rewards (1st place bonus, etc.)\n"
                "• **Rush Mode:** Points converted at checkpoints with multipliers\n"
                "• Solo and Multi WR tracked separately\n\n"
                "**Tips for Fast Progression:**\n"
                "• Maintain daily streaks for multiplier bonuses\n"
                "• Solve quickly for speed bonuses (under 60s)\n"
                "• Win consistently to climb tiers faster\n"
                "• Higher tiers give better multipliers on all rewards\n"
                "• Race Mode offers competitive high-reward opportunities\n\n"
                "*Use `/profile` to track your stats, `/leaderboard` to see rankings!*"
            )
            
            # Tiers Section
            tier_text = "\n".join([
                f"{EMOJIS.get(t['icon'], t['icon'])} **{t['name']}** - WR ≥ {t['min_wr']} (×{t['multiplier']} rewards)" 
                for t in TIERS
            ])
            embed.add_field(name="🏆 Ranking Tiers", value=tier_text, inline=False)
        else:
            embed = discord.Embed(title="❓ Unknown Feature", description="Feature not found.", color=discord.Color.red())

        embed.set_footer(text="Use 'Basic' or 'Show More' to navigate back.")
        return embed
