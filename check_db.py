"""Quick MySQL check. Run:  python check_db.py"""
import config
from db import db, ensure_database

try:
    ensure_database()
    with db() as c:
        print(f"Connected to MySQL {config.MYSQL_HOST}:{config.MYSQL_PORT}, database '{config.MYSQL_DATABASE}'")
        for table in ("users", "admins", "chat_logs", "chunks"):
            try:
                n = c.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
                print(f"  {table}: {n} rows")
            except Exception:
                print(f"  {table}: not created yet (start the app once with: python app.py)")
except Exception as e:
    print("FAILED:", e)
