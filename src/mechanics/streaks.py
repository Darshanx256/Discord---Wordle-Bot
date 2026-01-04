import datetime
import random
from src.utils import EMOJIS

class StreakManager:
    def __init__(self, bot):
        self.bot = bot
        self.messages = {
            1: "✨ **Day 1: The Journey Begins!** 🥚\nPlay 3 days in a row to ignite your **Double WR Bonus**!",
            2: "🔥 **Day 2: Keep the Flame Alive!** 🐣\nJust one more day until you unlock the power of **Double WR**!",
            3: f"⚡ **Day 3: Supercharged!** 🙀\n**2x WR ACTIVATED** {EMOJIS.get('fire','🔥')}\nYour first 3 wins every day now grant double rewards!",
            7: f"🛡️ **Day 7: The Guardian's Mark!** {EMOJIS.get('7_streak', '')}\nCongrats on your **Shiny 7-Day Badge**! You're becoming a legend.",
            8: "🐙 **Day 8: Deep Sea Determination!**\nReach Day 10 to evolve your rewards even further!",
            9: "👀 **Day 9: On the Horizon!**\nOne more sunrise to reach the next tier of power.",
            10: f"💠 **Day 10: Elite Status!** 🙀\n**2.5x WR ACTIVATED** {EMOJIS.get('fire','🔥')}\nYour first 4 wins every day are now heavily boosted!",
            13: f"🌶️ **Day 13: Sizzling Hot!** {EMOJIS.get('fire','🔥')}\nTomorrow brings a masterpiece for your profile.",
            14: f"🔥 **Day 14: Master of the Flame!** {EMOJIS.get('14_streak', '')}\nCongrats on the **Hot 14-Day Badge**! Absolute fire.",
            28: f"💎 **Day 28: Mythical Status!** {EMOJIS.get('28_streak', '')}\n**Mythical Flame Badge UNLOCKED!** You are among the elite.",
            35: f"👑 **Day 35: Absolute Sovereign!** 🥳\n**3x WR ACTIVATED** {EMOJIS.get('fire','🔥')}\nMaximum power achieved. Your first 4 wins are legendary.",
            40: "😶‍🌫️ **Day 40: Into the Mist!**\n10 more days to claim the ultimate **Dragon Badge**.",
            50: f"🐲 **Day 50: DRAGON LORD!** {EMOJIS.get('dragon', '')}\n**Dragon Milestone Badge UNLOCKED!** You have conquered Wordle."
        }
        
    def get_streak_message(self, streak_days):
        """Returns the appropriate ephemeral message for the streak day."""
        if streak_days in self.messages:
            return self.messages[streak_days]
        
        if 4 <= streak_days <= 6:
            return f"🪄 **{streak_days} Day Streak!**\nKeep it up for the **Shiny 7-Day Badge** {EMOJIS.get('7_streak', '')}"
        if 11 <= streak_days <= 12:
            return f"🔥 **{streak_days} Day Streak!**\nMake it to 14 for the **Hot 14-Day Badge**!"
        if 21 <= streak_days <= 27:
            return f"🌌 **{streak_days} Day Streak!**\nThe **Mythical 28-Day Badge** is within reach!"
        
        # Rotating messages for 50+
        if streak_days > 50:
            rotating = [
                "🚀 **To the moon!** Your momentum is unstoppable.",
                "💎 **Diamond focus!** Another day of perfection.",
                "🛑 **Unstoppable!** The streak has a mind of its own now.",
                "👑 **Absolute Legend.** Future generations will hear of this.",
                "⚡ **Electric performance!** Can you reach 100?"
            ]
            return f"✨ **Day {streak_days}:** {random.choice(rotating)}"
            
        return f"🔥 **{streak_days} Day Streak!** The fire burns bright!"

    def clear_inventory_bonuses(self, user_id):
        """Discards only specific streak badges (7, 14, 28) when a streak is broken."""
        try:
            # 1. Get current inventory
            res = self.bot.supabase_client.table('user_stats_v2').select('eggs, active_badge').eq('user_id', user_id).execute()
            if not res.data: return
            
            data = res.data[0]
            inventory = data.get('eggs') or {}
            active_badge = data.get('active_badge')
            
            # 2. Filter out streak badges
            streak_badge_ids = ['7_streak', '14_streak', '28_streak']
            for bid in streak_badge_ids:
                if bid in inventory:
                    del inventory[bid]
            
            # 3. Handle active badge conflict
            new_active = active_badge
            if active_badge in streak_badge_ids:
                new_active = None
                
            # 4. Update
            self.bot.supabase_client.table('user_stats_v2').update({
                'eggs': inventory, 
                'active_badge': new_active
            }).eq('user_id', user_id).execute()
        except Exception as e:
            print(f"Failed to clear streak badges on break: {e}")

    def check_streak(self, user_id):
        """
        Checks and updates streak for a user.
        Returns: (streak_message, multiplier, badge_awarded)
        """
        now = datetime.datetime.utcnow()
        today_date = now.date()
        
        try:
            # Fetch current streak info
            res = self.bot.supabase_client.table('streaks_v4').select('*').eq('user_id', user_id).execute()
            
            if not res.data:
                # First time playing
                self.bot.supabase_client.table('streaks_v4').insert({
                    'user_id': user_id,
                    'current_streak': 1,
                    'max_streak': 1,
                    'last_play_date': today_date.isoformat()
                }).execute()
                return self.get_streak_message(1), 1.0, None
            
            data = res.data[0]
            last_date_str = data['last_play_date']
            try:
                last_date = datetime.datetime.fromisoformat(last_date_str).date()
            except ValueError:
                last_date = datetime.datetime.strptime(last_date_str.split('T')[0], "%Y-%m-%d").date()
                
            delta_days = (today_date - last_date).days
            
            updated_streak = data['current_streak']
            message = None
            badge = None
            
            if delta_days == 0:
                return None, self.calculate_multiplier(updated_streak), None
                
            elif delta_days == 1:
                # Consecutive day!
                updated_streak += 1
                message = self.get_streak_message(updated_streak)
                # Check Badges
                badge = self.check_badge_reward(user_id, updated_streak)
                
            else:
                # Missed a day (or more). Reset.
                # DISCARD BADGES/BONUSES logic
                if updated_streak >= 1: 
                    self.clear_inventory_bonuses(user_id)
                
                updated_streak = 1
                message = f"💔 **Streak Broken!** (Your previous streak was {data['current_streak']} days)\nInventory cleared. New streak starts now: " + self.get_streak_message(1)
            
            # Update DB
            new_max = max(data['max_streak'], updated_streak)
            self.bot.supabase_client.table('streaks_v4').update({
                'current_streak': updated_streak,
                'max_streak': new_max,
                'last_play_date': today_date.isoformat()
            }).eq('user_id', user_id).execute()
            
            return message, self.calculate_multiplier(updated_streak), badge
            
        except Exception as e:
            print(f"Streak Error: {e}")
            return None, 1.0, None

    def calculate_multiplier(self, streak):
        """Calculates WR multiplier based on streak."""
        if streak >= 35: return 3.0
        if streak >= 10: return 2.5
        if streak >= 3: return 2.0
        return 1.0

    def check_badge_reward(self, user_id, streak):
        """Checks if a badge should be awarded/unlocked."""
        badge_id = None
        if streak == 7: badge_id = '7_streak'
        elif streak == 14: badge_id = '14_streak'
        elif streak == 28: badge_id = '28_streak'
        elif streak == 50: badge_id = 'dragon'
        
        if badge_id:
            try:
                # 1. Get current inventory
                res = self.bot.supabase_client.table('user_stats_v2').select('eggs').eq('user_id', user_id).execute()
                if res.data:
                    current_eggs = res.data[0].get('eggs') or {}
                    if badge_id not in current_eggs:
                        current_eggs[badge_id] = 1
                        self.bot.supabase_client.table('user_stats_v2').update({'eggs': current_eggs}).eq('user_id', user_id).execute()
                        return badge_id
            except Exception as e:
                print(f"Failed to award streak badge: {e}")
            
        return None

    def get_bonus_limit(self, streak):
        """Returns the number of wins that get the bonus based on streak."""
        if streak >= 10: return 4
        if streak >= 3: return 3
        return 0

    def get_user_multiplier(self, user_id):
        # Quick read only helper
        try:
             res = self.bot.supabase_client.table('streaks_v4').select('current_streak, last_play_date').eq('user_id', user_id).execute()
             if res.data:
                 # Check if streak is active (today or yesterday)
                 return self.calculate_multiplier(res.data[0]['current_streak'])
        except:
            pass
        return 1.0
