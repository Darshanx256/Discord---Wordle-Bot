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
    print("🚀 Starting Wordle Bot Application...")
    
    # 1. Start Flask Server in background thread
    print("🌐 Starting Flask web server...")
    t = threading.Thread(target=run_flask_server, daemon=True)
    t.start()
    
    # 2. Run Discord Bot
    print("🤖 Connecting to Discord...")
    try:
        bot.run(TOKEN)
    except Exception as e:
        print(f"❌ FATAL: Bot failed to start: {e}")
        sys.exit(1)
