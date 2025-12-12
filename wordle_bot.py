import threading
import sys
from src.config import TOKEN, SUPABASE_URL, SUPABASE_KEY
from src.server import run_flask_server
from src.bot import bot

if not TOKEN: 
    print("❌ FATAL: DISCORD_TOKEN not found.")
    sys.exit(1)

if not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ FATAL: SUPABASE_URL or SUPABASE_KEY (for Supabase client) not found.")
    sys.exit(1)

if __name__ == "__main__":
    t = threading.Thread(target=run_flask_server)
    t.start()
    bot.run(TOKEN)
