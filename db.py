import os
from dotenv import load_dotenv
from supabase import create_client
import logging

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise Exception("Missing Supabase credentials in .env")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
logger = logging.getLogger("db")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | db | %(levelname)s | %(message)s")
logger.info("[DB] Connected to Supabase")
