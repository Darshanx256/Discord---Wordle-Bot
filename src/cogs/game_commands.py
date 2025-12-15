"""
Game commands cog: /wordle, /wordle_classic, /solo, /show_solo, /cancel_solo, /stop_game
"""
import asyncio
import discord
from discord.ext import commands
from src.game import WordleGame
from src.database import get_next_secret, get_next_classic_secret
from src.utils import EMOJIS
from src.ui import SoloView, get_markdown_keypad_status


class GameCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="wordle", description="Start a new game (Simple word list).")
    async def start(self, ctx):
        if not ctx.guild:
            return await ctx.send("❌ Command must be used in a server.", ephemeral=True)
        if not self.bot.secrets:
            return await ctx.send("❌ Simple word list missing.", ephemeral=True)

        cid = ctx.channel.id
        if cid in self.bot.games:
            return await ctx.send("⚠️ Game already active. Use `/stop_game` to end it.", ephemeral=True)

        secret = get_next_secret(self.bot, ctx.guild.id)

        title = "✨ Wordle Started! (Simple)"
        embed = discord.Embed(title=title, color=discord.Color.blue())
        embed.description = f"A simple **5-letter word** has been chosen. **6 attempts** total."
        embed.add_field(name="How to Play", value="`/guess word:xxxxx`", inline=False)

        msg = await ctx.send(embed=embed)

        # Init Game
        self.bot.games[cid] = WordleGame(secret, cid, ctx.author, msg.id)
        print(f"DEBUG: Game STARTED in Channel {cid}. Active Games: {list(self.bot.games.keys())}")

    @commands.hybrid_command(name="wordle_classic", description="Start a Classic game (Hard word list).")
    async def start_classic(self, ctx):
        if not ctx.guild:
            return await ctx.send("❌ Command must be used in a server.", ephemeral=True)
        if not self.bot.hard_secrets:
            return await ctx.send("❌ Classic word list missing.", ephemeral=True)

        cid = ctx.channel.id
        if cid in self.bot.games:
            return await ctx.send("⚠️ Game already active.", ephemeral=True)

        secret = get_next_classic_secret(self.bot, ctx.guild.id)

        title = "⚔️ Wordle Started! (Classic)"
        embed = discord.Embed(title=title, color=discord.Color.dark_gold())
        embed.description = f"**Hard Mode!** 6 attempts."
        embed.add_field(name="How to Play", value="`/guess word:xxxxx`", inline=False)

        msg = await ctx.send(embed=embed)
        self.bot.games[cid] = WordleGame(secret, cid, ctx.author, msg.id)
        print(f"DEBUG: Classic Game STARTED in Channel {cid}. Active Games: {list(self.bot.games.keys())}")

    @commands.hybrid_command(name="solo", description="Play a private game (Ephemeral).")
    async def solo(self, ctx):
        if ctx.author.id in self.bot.solo_games:
            return await ctx.send("⚠️ You already have a solo game running!", ephemeral=True)

        secret = __import__('random').choice(self.bot.secrets)
        game = WordleGame(secret, 0, ctx.author, 0)
        self.bot.solo_games[ctx.author.id] = game

        board_display = "No guesses yet."
        keypad = get_markdown_keypad_status(game.used_letters, self.bot, ctx.author.id)
        progress_bar = "[○○○○○○]"

        embed = discord.Embed(title="Solo Wordle | Attempt 0/6", color=discord.Color.gold())
        embed.description = "This game is **private**. Only you can see it.\nUse the button below to guess."
        embed.add_field(name="Board", value=board_display, inline=False)
        embed.set_footer(text=f"6 tries left {progress_bar}")

        message_content = f"⌨️ **Keyboard Status:**\n{keypad}"

        view = SoloView(self.bot, game, ctx.author)
        await ctx.send(content=message_content, embed=embed, view=view, ephemeral=True)

    @commands.hybrid_command(name="show_solo", description="Show your active solo game (if dismissed).")
    async def show_solo(self, ctx):
        if ctx.author.id not in self.bot.solo_games:
            return await ctx.send("⚠️ No active solo game found.", ephemeral=True)

        game = self.bot.solo_games[ctx.author.id]

        filled = "●" * game.attempts_used
        empty = "○" * (6 - game.attempts_used)
        progress_bar = f"[{filled}{empty}]"

        board_display = "\n".join([f"{h['pattern']}" for h in game.history]) if game.history else "No guesses yet."
        keypad = get_markdown_keypad_status(game.used_letters, self.bot, ctx.author.id)

        embed = discord.Embed(title=f"Solo Wordle | Attempt {game.attempts_used}/6", color=discord.Color.gold())
        embed.add_field(name="Board", value=board_display, inline=False)
        embed.set_footer(text=f"{6 - game.attempts_used} tries left {progress_bar}")

        message_content = f"⌨️ **Keyboard Status:**\n{keypad}"

        view = SoloView(self.bot, game, ctx.author)
        await ctx.send(content=message_content, embed=embed, view=view, ephemeral=True)

    @commands.hybrid_command(name="cancel_solo", description="Cancel your active solo game.")
    async def cancel_solo(self, ctx):
        if ctx.author.id not in self.bot.solo_games:
            return await ctx.send("⚠️ No active solo game to cancel.", ephemeral=True)

        game = self.bot.solo_games.pop(ctx.author.id)
        await ctx.send(f"✅ Solo game cancelled. The word was **{game.secret.upper()}**.", ephemeral=True)

    @commands.hybrid_command(name="stop_game", description="Force stop the current game.")
    async def stop_game(self, ctx):
        cid = ctx.channel.id
        game = self.bot.games.get(cid)

        if not game:
            return await ctx.send("No active game to stop.", ephemeral=True)

        if (ctx.author.id == game.started_by.id) or ctx.author.guild_permissions.manage_messages:
            self.bot.stopped_games.add(cid)
            self.bot.games.pop(cid)
            await ctx.send(f"🛑 Game stopped. Word: **{game.secret.upper()}**.")

            async def _clear_stopped(ch_id):
                await asyncio.sleep(300)
                try:
                    self.bot.stopped_games.discard(ch_id)
                except:
                    pass

            asyncio.create_task(_clear_stopped(cid))
        else:
            await ctx.send("❌ Only Starter or Admin can stop it.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(GameCommands(bot))
