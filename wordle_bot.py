import threading
import sys
import signal
from src.config import TOKEN, SUPABASE_URL, SUPABASE_KEY
from src.server import run_flask_server, set_bot_instance
from src.bot import bot

if not TOKEN: 
    print("❌ FATAL: DISCORD_TOKEN not found.")
    sys.exit(1)

if not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ FATAL: SUPABASE_URL or SUPABASE_KEY (for Supabase client) not found.")
    sys.exit(1)

def handle_shutdown(signum, frame):
    """Handle graceful shutdown on SIGTERM (Cloud Run)."""
    print("\n⚠️ Shutdown signal received. Closing bot...")
    if not bot.is_closed():
        # Schedule the bot close
        import asyncio
        asyncio.run(bot.close())
    sys.exit(0)

if __name__ == "__main__":
    # Register signal handler for graceful shutdown
    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)
    
    # Pass bot instance to server for health checks
    set_bot_instance(bot)
    
    # Start Flask server in a separate thread
    t = threading.Thread(target=run_flask_server, daemon=True)
    t.start()
    
    # Run the bot (this blocks until bot.close() is called)
    try:
        bot.run(TOKEN)
    except KeyboardInterrupt:
        print("\n✅ Bot shutdown complete.")
        sys.exit(0)
