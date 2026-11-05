import hmac
import re
from pathlib import Path

from flask import Flask, g, jsonify, redirect, render_template, request
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename

import auth
import config
import kb
import rag

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25 MB per request
auth.init_db()
kb.init_kb()

ALLOWED_EXT = (".pdf", ".txt", ".md")

# Startup check: tell the developer exactly what is missing and where Flask looks.
for _f in ["css/style.css", "js/common.js", "js/auth.js", "js/chat.js", "js/admin.js"]:
    _p = Path(app.static_folder) / _f
    if not _p.is_file():
        print(f"WARNING: static file missing -> {_p}")
for _f in ["base", "login", "register", "chat", "admin"]:
    _p = Path(app.template_folder or "templates")
    _p = Path(app.root_path) / _p / f"{_f}.html"
    if not _p.is_file():
        print(f"WARNING: template missing -> {_p}")

USERNAME_RE = re.compile(r"[A-Za-z0-9_.@+-]{3,60}")


@app.context_processor
def inject_company():
    return {"company": config.COMPANY_NAME}


@app.after_request
def security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Content-Security-Policy"] = "default-src 'self'"
    return resp


@app.errorhandler(413)
def too_large(_):
    return jsonify(error="File too large (max 25 MB)"), 413


# ---------------- pages ----------------
@app.get("/")
def index():
    user = auth.user_from_cookie()
    if not user:
        return redirect("/login")
    return redirect("/admin" if user["role"] == "admin" else "/chat")


@app.get("/login")
def login_page():
    return render_template("login.html")


@app.get("/register")
def register_page():
    return render_template("register.html", admin_enabled=bool(config.ADMIN_SIGNUP_CODE))


@app.get("/chat")
@auth.page_auth()
def chat_page():
    return render_template("chat.html", user=g.user)


@app.get("/admin")
@auth.page_auth("admin")
def admin_page():
    return render_template("admin.html", user=g.user)


# ---------------- auth API ----------------
@app.post("/api/auth/register")
def register():
    d = request.get_json(silent=True) or {}
    username = str(d.get("username") or "").strip()
    password = str(d.get("password") or "")
    role = d.get("role", "user")

    if not USERNAME_RE.fullmatch(username):
        return jsonify(error="Username must be 3-60 characters: letters, numbers, and . _ @ + -"), 400
    if len(password) < 8:
        return jsonify(error="Password must be at least 8 characters"), 400
    if role not in ("user", "admin"):
        return jsonify(error="Invalid account type"), 400
    if role == "admin":
        code = str(d.get("admin_code") or "")
        if not config.ADMIN_SIGNUP_CODE or not hmac.compare_digest(
            code.encode(), config.ADMIN_SIGNUP_CODE.encode()
        ):
            return jsonify(error="Invalid admin code"), 403
    if auth.username_exists(username):
        return jsonify(error="That username is already taken"), 409
    try:
        auth.create_account(username, password, role)
    except auth.IntegrityError:
        return jsonify(error="That username is already taken"), 409
    return jsonify(message="Account created. Please log in."), 201


@app.post("/api/auth/login")
def login():
    d = request.get_json(silent=True) or {}
    username = str(d.get("username") or "").strip()
    password = str(d.get("password") or "")

    user = auth.get_account_by_username(username)
    ok = check_password_hash(user["password_hash"] if user else auth.DUMMY_HASH, password)
    if not user or not ok:
        return jsonify(error="Invalid username or password"), 401

    token = auth.make_token(user)
    resp = jsonify(token=token, token_type="Bearer",
                   expires_in=config.TOKEN_HOURS * 3600,
                   username=user["username"], role=user["role"])
    resp.set_cookie(auth.COOKIE_NAME, token, httponly=True, samesite="Lax",
                    secure=config.COOKIE_SECURE, max_age=config.TOKEN_HOURS * 3600)
    return resp


@app.post("/api/auth/logout")
def logout():
    resp = jsonify(message="Logged out")
    resp.delete_cookie(auth.COOKIE_NAME)
    return resp


@app.get("/api/auth/me")
@auth.api_auth()
def me():
    return jsonify(username=g.user["username"], role=g.user["role"])


# ---------------- chat API ----------------
@app.post("/api/chat")
@auth.api_auth()
def chat():
    question = str((request.get_json(silent=True) or {}).get("question") or "").strip()
    if not question or len(question) > 1000:
        return jsonify(error="Question must be 1-1000 characters"), 400
    try:
        answer, sources, found = rag.ask(question)
    except Exception:
        app.logger.exception("RAG failure")
        return jsonify(error="The assistant is unavailable right now. Please try again."), 500
    auth.log_chat(g.user, question, answer, sources, found)
    return jsonify(answer=answer, sources=sources)


# ---------------- admin API ----------------
@app.get("/api/admin/documents")
@auth.api_auth("admin")
def list_docs():
    docs = kb.list_documents()
    return jsonify(documents=[{"name": n, "chunks": c} for n, c in sorted(docs.items())])


@app.post("/api/admin/upload")
@auth.api_auth("admin")
def upload():
    results = []
    for f in request.files.getlist("files"):
        name = secure_filename(f.filename or "")
        if not name.lower().endswith(ALLOWED_EXT):
            results.append({"name": f.filename, "error": "Only PDF, TXT or MD files are allowed"})
            continue
        path = config.DOCS_DIR / name
        f.save(path)
        try:
            results.append({"name": name, "chunks": kb.ingest_file(path)})
        except ValueError as e:
            path.unlink(missing_ok=True)
            results.append({"name": name, "error": str(e)})
        except Exception:
            app.logger.exception("Ingest failed for %s", name)
            path.unlink(missing_ok=True)
            results.append({"name": name, "error": "Could not read or index this file"})
    return jsonify(results=results)


@app.delete("/api/admin/documents/<name>")
@auth.api_auth("admin")
def delete_doc(name):
    if secure_filename(name) != name:
        return jsonify(error="Invalid file name"), 400
    kb.delete_document(name)
    return jsonify(message="Deleted")


@app.post("/api/admin/rebuild")
@auth.api_auth("admin")
def rebuild():
    try:
        return jsonify(results=kb.ingest_all())
    except Exception:
        app.logger.exception("Rebuild failed")
        return jsonify(error="Rebuild failed. Check the server log."), 500


@app.get("/api/admin/users")
@auth.api_auth("admin")
def list_users():
    """Users and admins, read from their two separate tables."""
    with auth.db() as c:
        rows = c.execute(
            "SELECT u.username AS username, 'user' AS role, u.created_at AS created_at, "
            "(SELECT COUNT(*) FROM chat_logs l WHERE l.account_type = 'user' AND l.account_id = u.id) AS questions "
            "FROM users u "
            "UNION ALL "
            "SELECT a.username, 'admin', a.created_at, "
            "(SELECT COUNT(*) FROM chat_logs l WHERE l.account_type = 'admin' AND l.account_id = a.id) "
            "FROM admins a ORDER BY created_at DESC"
        ).fetchall()
    return jsonify(users=[dict(r) for r in rows])


@app.get("/api/admin/unanswered")
@auth.api_auth("admin")
def unanswered():
    with auth.db() as c:
        rows = c.execute(
            "SELECT question, created_at, username FROM chat_logs WHERE answered = 0 "
            "ORDER BY id DESC LIMIT 50"
        ).fetchall()
    return jsonify(items=[dict(r) for r in rows])


@app.get("/api/admin/chats")
@auth.api_auth("admin")
def chat_history():
    """Everything people typed, with the bot's answer (newest first)."""
    with auth.db() as c:
        rows = c.execute(
            "SELECT username, account_type AS role, question, answer, sources, answered, created_at "
            "FROM chat_logs ORDER BY id DESC LIMIT 200"
        ).fetchall()
    return jsonify(chats=[dict(r) for r in rows])


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
