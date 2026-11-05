"""Accounts (separate MySQL tables for users and admins), chat logs, JWT tokens and route guards."""
import datetime as dt
from functools import wraps

import jwt
from flask import g, jsonify, redirect, request
from werkzeug.security import generate_password_hash

import config
from db import IntegrityError, db, ensure_database  # noqa: F401  (re-exported)

DUMMY_HASH = generate_password_hash("not-a-real-password")  # equalises login timing
COOKIE_NAME = "access_token"


# ---------- database (MySQL): one table per purpose ----------
#   users      -> normal users          admins -> admins (separate table)
#   chat_logs  -> every question typed + the answer      chunks -> the KB (see kb.py)
TABLE = {"user": "users", "admin": "admins"}   # role -> table name


def init_db():
    ensure_database()
    with db() as c:
        for table in TABLE.values():
            c.execute(
                f"""CREATE TABLE IF NOT EXISTS {table} (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    username VARCHAR(60) NOT NULL UNIQUE,
                    password_hash VARCHAR(255) NOT NULL,
                    created_at VARCHAR(40) NOT NULL
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"""
            )
        c.execute(
            """CREATE TABLE IF NOT EXISTS chat_logs (
                id INT AUTO_INCREMENT PRIMARY KEY,
                account_type ENUM('user','admin') NOT NULL,
                account_id INT NOT NULL,
                username VARCHAR(60) NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                sources VARCHAR(500) NOT NULL DEFAULT '',
                answered TINYINT(1) NOT NULL,
                created_at VARCHAR(40) NOT NULL,
                INDEX idx_chat_account (account_type, account_id),
                INDEX idx_chat_answered (answered)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"""
        )


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def username_exists(username):
    """A username is unique across BOTH the users and admins tables."""
    with db() as c:
        return any(c.execute(f"SELECT 1 AS x FROM {t} WHERE username = ?", (username,)).fetchone()
                   for t in TABLE.values())


def create_account(username, password, role):
    """role 'user' -> users table, role 'admin' -> admins table."""
    with db() as c:
        c.execute(
            f"INSERT INTO {TABLE[role]} (username, password_hash, created_at) VALUES (?,?,?)",
            (username, generate_password_hash(password), _now()),
        )


def get_account_by_username(username):
    with db() as c:
        for role, table in TABLE.items():
            row = c.execute(f"SELECT * FROM {table} WHERE username = ?", (username,)).fetchone()
            if row:
                row["role"] = role
                return row
    return None


def get_account(role, account_id):
    if role not in TABLE:
        return None
    with db() as c:
        row = c.execute(f"SELECT * FROM {TABLE[role]} WHERE id = ?", (account_id,)).fetchone()
    if row:
        row["role"] = role
    return row


def log_chat(account, question, answer, sources, answered):
    """Store what the person typed and what the bot answered."""
    with db() as c:
        c.execute(
            "INSERT INTO chat_logs (account_type, account_id, username, question, answer, sources, answered, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (account["role"], account["id"], account["username"], question, answer,
             ", ".join(sources)[:500], int(answered), _now()),
        )


# ---------- tokens ----------
def make_token(user):
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "sub": str(user["id"]),
        "username": user["username"],
        "role": user["role"],
        "iat": now,
        "exp": now + dt.timedelta(hours=config.TOKEN_HOURS),
    }
    return jwt.encode(payload, config.SECRET_KEY, algorithm="HS256")


def _user_from_token(token):
    if not token:
        return None
    try:
        claims = jwt.decode(token, config.SECRET_KEY, algorithms=["HS256"])
        return get_account(claims["role"], int(claims["sub"]))  # rejects tokens of deleted accounts
    except (jwt.PyJWTError, KeyError, ValueError):
        return None


def user_from_cookie():
    return _user_from_token(request.cookies.get(COOKIE_NAME))


def user_from_bearer():
    header = request.headers.get("Authorization", "")
    return _user_from_token(header[7:]) if header.startswith("Bearer ") else None


# ---------- guards ----------
def api_auth(role=None):
    """API routes: token must be sent in the Authorization: Bearer header."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = user_from_bearer()
            if not user:
                return jsonify(error="Authentication required"), 401
            if role and user["role"] != role:
                return jsonify(error="You do not have permission"), 403
            g.user = user
            return fn(*args, **kwargs)
        return wrapper
    return deco


def page_auth(role=None):
    """HTML pages: logged in via the HttpOnly cookie set at login."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = user_from_cookie()
            if not user:
                return redirect("/login")
            if role and user["role"] != role:
                return redirect("/chat")
            g.user = user
            return fn(*args, **kwargs)
        return wrapper
    return deco
