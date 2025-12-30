
import asyncio
import os
import sys
from dotenv import load_dotenv

# Force load .env from current directory (c:\Users\Darshan\Downloads\Discord---Wordle-Bot)
load_dotenv()

# Mock Discord Context
class MockBot:
    def __init__(self):
        self.name_cache = {}
        from supabase import create_client
        # Load from env or config
        from src.config import SUPABASE_URL, SUPABASE_KEY
        self.supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
    
    def get_user(self, uid):
        return None

    async def fetch_user(self, uid):
        raise Exception("Mock fetch failed")

async def test_leaderboard_query():
    print("Testing Supabase Query...")
    try:
        sys.path.append(os.getcwd())
        from src.config import SUPABASE_URL, SUPABASE_KEY
        from supabase import create_client
        
        print(f"DEBUG: URL={SUPABASE_URL}") 
        
        if not SUPABASE_URL:
            print("❌ SUPABASE_URL is missing!")
            return

        client = create_client(SUPABASE_URL, SUPABASE_KEY)
        
        # Test 1: Count Query
        print("1. Testing Count Query...")
        count_res = client.table('user_stats_v2') \
            .select('user_id', count='exact', head=True) \
            .execute()
        total_count = count_res.count
        print(f"   Count result: {total_count}")

        # Test 2: Data Query
        print("2. Testing Data Query...")
        response = client.table('user_stats_v2') \
            .select('user_id, multi_wins, xp, multi_wr, active_badge') \
            .order('multi_wr', desc=True) \
            .limit(50) \
            .execute()
        
        print(f"   Data result rows: {len(response.data)}")
        if response.data:
            print(f"   Sample row: {response.data[0]}")
            
    except Exception as e:
        print(f"❌ DB ERROR: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_leaderboard_query())
