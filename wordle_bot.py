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
    print("🚀 Initializing Wordle Bot System...")
    
    # Start the Flask server in a background thread
    print(f"🌐 Starting Flask server thread...")
    flask_thread = threading.Thread(target=run_flask_server, daemon=True)
    flask_thread.start()
    
    # Start the Discord bot in the main thread
    print("🤖 Starting Discord bot...")
    try:
        bot.run(TOKEN)
    except Exception as e:
        print(f"❌ FATAL: Discord bot failed to start: {e}")
        sys.exit(1)

