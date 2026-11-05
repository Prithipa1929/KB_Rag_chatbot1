import os
import secrets
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent
DOCS_DIR = BASE_DIR / "company_docs"

# MySQL (users, chunks and chat logs are stored here)
MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "kb_chatbot")

COMPANY_NAME = os.getenv("COMPANY_NAME", "Acme Corp")

# Optional: with a Gemini key the answer is written by the LLM; without it the bot
# returns the best-matching passages from your documents.
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-2.5-flash")

CHUNK_SIZE = 800
CHUNK_OVERLAP = 150
TOP_K = 4
MIN_SCORE = 0.12   # below this similarity the bot says "Not available in KB"

SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    SECRET_KEY = secrets.token_hex(32)
    print("WARNING: SECRET_KEY not set. Using a temporary key; all logins reset on restart.")
ADMIN_SIGNUP_CODE = os.getenv("ADMIN_SIGNUP_CODE", "")
TOKEN_HOURS = int(os.getenv("TOKEN_HOURS", "8"))
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0") == "1"

DOCS_DIR.mkdir(exist_ok=True)
