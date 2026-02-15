import os
from dotenv import load_dotenv

try:
    load_dotenv()
except Exception:
    pass

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Ordered from highest to lowest threshold.
TIERS = [
    {"name": "Legend", "min_wr": 3900},
    {"name": "Grandmaster", "min_wr": 2800},
    {"name": "Master", "min_wr": 1600},
    {"name": "Elite", "min_wr": 900},
    {"name": "Challenger", "min_wr": 0},
]
