import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

# ── AI Model ────────────────────────────────────────────────
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_TEMPERATURE = 0.3
GEMINI_MAX_TOKENS = 4096

# ── Monday.com ──────────────────────────────────────────────
MONDAY_API_KEY = os.environ["MONDAY_API_KEY"]
MONDAY_API_URL = "https://api.monday.com/v2"

BOARD_IDS = {
    "sales":   int(os.environ.get("MONDAY_SALES_BOARD_ID",   "5027332893")),
    "artists": int(os.environ.get("MONDAY_ARTISTS_BOARD_ID", "5027403725")),
    "staff":   int(os.environ.get("MONDAY_STAFF_BOARD_ID",   "5027403709")),
}

# ── Group Context ────────────────────────────────────────────
GROUP_CONTEXT_MAX_MESSAGES = 50   # circular buffer size per group
GROUP_CONTEXT_WINDOW = 20         # messages injected into model context

# ── Confirmation ─────────────────────────────────────────────
CONFIRMATION_TTL_SECONDS = 120    # pending write expires after 2 minutes

# ── Access Control ────────────────────────────────────────────
ALLOWED_CHAT_IDS_STR = os.environ.get("ALLOWED_CHAT_IDS", "")
ALLOWED_CHAT_IDS = {
    int(x.strip()) for x in ALLOWED_CHAT_IDS_STR.split(",") if x.strip()
} if ALLOWED_CHAT_IDS_STR else set()

# ── Rate Limiting ─────────────────────────────────────────────
RATE_LIMIT_MESSAGES = 10
RATE_LIMIT_WINDOW_SECONDS = 60

# ── Prompt ───────────────────────────────────────────────────
import pathlib
PROMPT_FILE = pathlib.Path(__file__).parent / "prompts" / "ARIA_v7_System_Prompt.md"
PROMPT_CACHE_TTL_SECONDS = 60
