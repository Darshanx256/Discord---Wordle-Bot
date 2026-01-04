import datetime
import random
from src.utils import EMOJIS

class StreakManager:
    def __init__(self, bot):
        self.bot = bot
        self.messages = {
            1: "🥚 1 day streak! Play 3 days in a row to get double WR bonus!",
            2: "🐣 2 days streak! Just one more day for double WR bonus!",
            3: f"🙀 3 days streak! 2x WR activated{EMOJIS.get('fire','🔥')} Your first 3 wins everyday will give 2x more WR!",
            # 4-6 handled dynamically
            7: f"{EMOJIS.get('7_streak', '')} 7 day streak! Congrats on your New Shiny Badge! Keep up with the streak and keep winning!",
            8: "🐙 8 days streak! Do it a Ten to get improved bonus!",
            9: "👀 9 days streak! One more day to get improved bonus!",
            10: f"🙀 10 days streak! 2.5x WR activated{EMOJIS.get('fire','🔥')} Your first 4 wins everyday will give increased WR!",
            # 11-12 handled dynamically
            13: f"{EMOJIS.get('fire','🔥')} 13 days streak! Just one day for a super hot surprise!",
            14: f"{EMOJIS.get('14_streak', '')} 14 day streak! Congrats on your Hot New Badge for the Hot You! Keep up with the streak and keep winning!",
            # 21 handled dynamically
            28: f"{EMOJIS.get('28_streak', '')} 28 days streak! You Rock! You have got mythical flame badge!",
            35: f"🥳 35 days streak! 3x WR activated{EMOJIS.get('fire','🔥')} Your first 4 wins everyday will give increased WR!",
            40: "😶‍🌫️ 40 days streak! 10 more to get Dragon Badge!",
            50: f"{EMOJIS.get('dragon', '')} 50 days streak! Congrats on reaching this milestone! You have won the dragon badge!"
        }
        
    def get_streak_message(self, streak_days):
        """Returns the appropriate ephemeral message for the streak day."""
        if streak_days in self.messages:
            return self.messages[streak_days]
        
        if 4 <= streak_days <= 6:
            return f"🪄 {streak_days} days streak! Do a 7 day streak to get a shiny badge {EMOJIS.get('7_streak', '')}"
        if 11 <= streak_days <= 12:
            return f"🔥 {streak_days} days streak! Make it 14 for a super hot surprise!"
        if streak_days == 21:
            return "[3x WR logic active] Keep going!" # Placeholder or add flavor
        
        # Rotating messages for 50+
        if streak_days > 50:
            rotating = [
                "🚀 To the moon! Streak continues!",
                "💎 Diamond hands! Another day, another win!",
                "🛑 Unstoppable! The streak lives on.",
                "👑 Absolute Legend. You're crushing it.",
                "⚡ Electric! Can you reach 100?"
            ]
            return f"✨ {streak_days} days streak! {random.choice(rotating)}"
            
        return f"🔥 {streak_days} days streak! Keep it up!"

    def check_streak(self, user_id):
        """
        Checks and updates streak for a user.
        Returns: (streak_message, multiplier, badge_awarded)
        """
        # This function should be called ONCE per day per user (effectively)
        # But since we don't want to spam DB, we check date first.
        
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
            # Parse date safely
            try:
                last_date = datetime.datetime.fromisoformat(last_date_str).date()
            except ValueError:
                # Handle Z format or other issues if necessary
                last_date = datetime.datetime.strptime(last_date_str.split('T')[0], "%Y-%m-%d").date()
                
            delta_days = (today_date - last_date).days
            
            updated_streak = data['current_streak']
            message = None
            badge = None
            
            if delta_days == 0:
                # Already played today. No streak update.
                # Just return current multiplier status?
                # or return None to indicate "no new streak event"
                return None, self.calculate_multiplier(updated_streak), None
                
            elif delta_days == 1:
                # Consecutive day!
                updated_streak += 1
                message = self.get_streak_message(updated_streak)
                # Check Badges
                badge = self.check_badge_reward(updated_streak)
                
            else:
                # Missed a day (or more). Reset.
                updated_streak = 1
                message = self.get_streak_message(1)
            
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

    def check_badge_reward(self, streak):
        """Checks if a badge should be awarded/unlocked."""
        badge_id = None
        if streak == 7: badge_id = '7_streak'
        elif streak == 14: badge_id = '14_streak'
        elif streak == 28: badge_id = '28_streak'
        elif streak == 50: badge_id = 'dragon_badge' # Special dragon badge
        
        if badge_id:
            # Add to user's 'eggs' inventory in user_stats_v2
            # We use a raw RPC or logic to append to JSONB would be best, 
            # but reading -> updating is safer for consistency if no specialized RPC exists.
            try:
                # 1. Get current inventory
                res = self.bot.supabase_client.table('user_stats_v2').select('eggs').eq('user_id', self.user_id_context).execute()
                if res.data:
                    current_eggs = res.data[0].get('eggs') or {}
                    # 2. Add badge if not present
                    if badge_id not in current_eggs:
                        current_eggs[badge_id] = 1
                        # 3. Update
                        self.bot.supabase_client.table('user_stats_v2').update({'eggs': current_eggs}).eq('user_id', self.user_id_context).execute()
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
