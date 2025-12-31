import os
import asyncio
from supabase import create_client

async def diagnose():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        print("Missing env vars")
        return
        
    client = create_client(url, key)
    
    # 1. Check legacy counts
    try:
        simple = client.table('guild_history').select('count', count='exact').execute()
        classic = client.table('guild_history_classic').select('count', count='exact').execute()
        print(f"Legacy Simple Count: {simple.count}")
        print(f"Legacy Classic Count: {classic.count}")
    except Exception as e:
        print(f"Error checking legacy tables: {e}")
        
    # 2. Check new bitset count
    try:
        pools = client.table('guild_word_pools').select('count', count='exact').execute()
        print(f"New Bitset Count: {pools.count}")
    except Exception as e:
        print(f"Error checking new bitset table: {e}")

if __name__ == "__main__":
    asyncio.run(diagnose())
