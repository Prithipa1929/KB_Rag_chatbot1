"""MySQL connection helper (PyMySQL). Usage: `with db() as c: c.execute(...)`."""
import re

import pymysql
from pymysql.cursors import DictCursor
from pymysql.err import IntegrityError  # re-exported so other files can catch it

import config


def _connect(database=None):
    return pymysql.connect(
        host=config.MYSQL_HOST, port=config.MYSQL_PORT,
        user=config.MYSQL_USER, password=config.MYSQL_PASSWORD,
        database=database, charset="utf8mb4", cursorclass=DictCursor, autocommit=False,
    )


class _Conn:
    """Commits on success, rolls back on error, always closes the connection."""

    def __init__(self):
        self._c = _connect(config.MYSQL_DATABASE)

    def execute(self, sql, params=()):
        cur = self._c.cursor()
        cur.execute(sql.replace("?", "%s"), params)   # code below writes ? placeholders
        return cur

    def executemany(self, sql, seq):
        cur = self._c.cursor()
        cur.executemany(sql.replace("?", "%s"), seq)
        return cur

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self._c.commit() if exc_type is None else self._c.rollback()
        finally:
            self._c.close()
        return False


def db():
    return _Conn()


def ensure_database():
    """Create the database if it does not exist yet."""
    name = config.MYSQL_DATABASE
    if not re.fullmatch(r"[A-Za-z0-9_]+", name):
        raise RuntimeError("MYSQL_DATABASE may contain only letters, numbers and _")
    try:
        conn = _connect()
    except pymysql.err.OperationalError as e:
        raise RuntimeError(
            f"Cannot connect to MySQL at {config.MYSQL_HOST}:{config.MYSQL_PORT} "
            f"as '{config.MYSQL_USER}'. Check that MySQL is running and the MYSQL_* values in .env "
            f"are correct. ({e})") from e
    with conn.cursor() as cur:
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{name}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    conn.commit()
    conn.close()
