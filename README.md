# KB Chatbot (Python + Flask + HTML/CSS)

User and Admin register/login with JWT tokens, an admin panel to upload company documents,
and a chatbot that answers only from those documents. **No API key needed.**

## Run
Requires a running MySQL server (users, KB chunks and chat logs are stored in MySQL).
```bash
pip install -r requirements.txt
cp .env.example .env        # set SECRET_KEY, ADMIN_SIGNUP_CODE and the MYSQL_* values
python app.py               # open http://127.0.0.1:5000
```

## Use
1. `/register` -> choose **Admin**, enter your ADMIN_SIGNUP_CODE -> register -> log in -> `/admin`.
2. Upload PDF / TXT / MD files (policies, FAQs, manuals).
3. Register a normal **User** (or open another browser) -> `/chat` -> ask questions.

## Answer format
- Greetings, thanks, bye and "company name" work without searching the documents.
- Every answer is a **bold title** plus bullet points, and only the sentences that match the question.
- Set `COMPANY_NAME` in `.env` (for example `COMPANY_NAME=Zentora Labs`) so "what is the company name" answers correctly.
- After updating from an older version, open `/admin` and click **Rebuild all** once, so documents are re-indexed with section headings.

## How the RAG works
Upload -> text split into ~800-character chunks -> stored in SQLite -> a question is matched to the
most similar chunks with TF-IDF -> the best passages are returned with file name and page.
If nothing matches well enough: "Not available in KB".

## Optional: LLM-written answers
Add `GOOGLE_API_KEY=...` to `.env`. The retrieved chunks are then sent to Gemini, which writes a
natural answer from them. If the call fails, the bot falls back to showing the passages.

## Limits (honest)
- TF-IDF matches **words**, not meaning. "vacation" will not find a document that says "leave".
  For meaning-based search, use embeddings (the earlier LangChain + Chroma project).
- Best with English documents. Scanned PDFs need OCR first.
- Set a fixed SECRET_KEY, use HTTPS (COOKIE_SECURE=1) and add login rate limiting before going live.

## MySQL setup (one time)
1. Install MySQL Server (Windows: MySQL Installer, or XAMPP) and remember the root password.
2. Run `setup_mysql.sql` in MySQL Workbench. It creates the database and a `kbuser` user (change the password inside).
3. Put the same values in `.env`: MYSQL_USER=kbuser, MYSQL_PASSWORD=..., MYSQL_DATABASE=kb_chatbot.
4. `python check_db.py` should print "Connected to MySQL ...".
5. `python app.py`. Tables `users`, `admins`, `chat_logs` and `chunks` are created automatically.


## Database layout (MySQL database `kb_chatbot`, one table per purpose)
| Table | What it stores |
|---|---|
| `users` | Normal users: username, hashed password, joined date |
| `admins` | Admins, in their own table |
| `chat_logs` | Every question a person typed + the bot's answer, sources, who asked (user/admin) and whether it was found |
| `chunks` | The knowledge base: pieces of the uploaded documents |

A username is unique across `users` and `admins`. The admin panel shows these under **Users**, **Chat history**
and **Unanswered questions**. If you ran an older version of this project, drop its database first
(`DROP DATABASE kb_chatbot;`) so the new tables are created.
