"""Knowledge base: chunks stored in SQLite, searched with TF-IDF (no API key needed).

Documents are split into sections (heading + paragraph) so every chunk knows its heading.
"""
import difflib
import re
import threading
from collections import Counter
from pathlib import Path

from pypdf import PdfReader
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel

import config
from auth import db

SUPPORTED = (".pdf", ".txt", ".md")
_lock = threading.Lock()
_index = {"sig": None, "vec": None, "matrix": None, "rows": None, "vocab": [], "stems": set()}
_PAGE_NO = re.compile(r"^(page\s*)?\d{1,4}(\s*(/|of)\s*\d{1,4})?$", re.I)


def init_kb():
    with db() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS chunks (
                id INT AUTO_INCREMENT PRIMARY KEY,
                source_file VARCHAR(255) NOT NULL,
                page_number INT NOT NULL,
                text TEXT NOT NULL,
                INDEX idx_chunks_file (source_file)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"""
        )


# ---------- reading ----------
def _strip_headers_footers(pages):
    """Remove page numbers and running headers/footers that repeat on many pages."""
    norm = lambda l: re.sub(r"\d+", "#", re.sub(r"\s+", " ", l.strip().lower()))
    counts = Counter()
    for _, t in pages:
        for k in {norm(x) for x in t.splitlines() if x.strip()}:
            counts[k] += 1
    thr = max(3, int(len(pages) * 0.4)) if len(pages) >= 4 else 10**9
    seen, out = set(), []
    for no, t in pages:
        keep = []
        for line in t.splitlines():
            s = line.strip()
            if s and _PAGE_NO.match(s):
                continue
            k = norm(line)
            if s and counts[k] >= thr:
                if k in seen:       # keep the first occurrence only
                    continue
                seen.add(k)
                keep.append(line)
                keep.append("")     # blank line so it never glues onto the next heading
                continue
            keep.append(line)
        out.append((no, "\n".join(keep)))
    return out


def _read_pages(path: Path):
    """Return [(page_number, text)]. page_number 0 means 'no pages' (txt/md)."""
    if path.suffix.lower() == ".pdf":
        reader = PdfReader(str(path))
        return _strip_headers_footers([(i + 1, p.extract_text() or "") for i, p in enumerate(reader.pages)])
    return [(0, path.read_text(encoding="utf-8", errors="ignore"))]


# ---------- sections and chunks ----------
def _is_heading(line: str) -> bool:
    core = re.sub(r"^\d+(\.\d+)*\.?\s+", "", line).rstrip(":").strip()   # drop "9." or "2.1" numbering
    words = core.split()
    return (1 <= len(words) <= 8 and len(core) <= 70
            and not re.search(r"[.,;:!?\d]", core)
            and re.match(r"[A-Z]", core) is not None)


def _sections(text: str, heading: str = ""):
    """Split page text into [(heading, paragraph)]. Returns (sections, last_heading)."""
    sections, buf, prev_end = [], [], True
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if not line:
            prev_end = True
            continue
        if prev_end and _is_heading(line):
            if buf:
                sections.append((heading, " ".join(buf)))
                buf = []
            heading = re.sub(r"^\d+(\.\d+)*\.?\s+", "", line).rstrip(":").strip()
            continue
        buf.append(line)
        prev_end = bool(re.search(r"[.!?:]$", line))
    if buf:
        sections.append((heading, " ".join(buf)))
    return sections, heading


def _chunk(text: str):
    """Sliding window over one paragraph; cuts at sentence/word boundaries, never mid-word."""
    text = re.sub(r"\s+", " ", text).strip()
    size, overlap, n = config.CHUNK_SIZE, config.CHUNK_OVERLAP, len(text)
    out, start = [], 0
    while start < n:
        end = min(start + size, n)
        if end < n:
            cut = max(text.rfind(". ", start, end) + 1, text.rfind(" ", start, end))
            if cut > start + size // 2:
                end = cut
        piece = text[start:end].strip()
        if piece:
            out.append(piece)
        if end >= n:
            break
        start = max(end - overlap, start + 1)
        s = text.find(". ", start, end)           # restart at a sentence start if possible
        if s != -1:
            start = s + 2
        else:
            sp = text.find(" ", start, end)       # otherwise at a word start
            start = sp + 1 if sp != -1 else end
    return out


# ---------- ingest / manage ----------
def ingest_file(path: Path) -> int:
    """Index one file. Re-ingesting the same file name replaces its old chunks."""
    rows, heading = [], ""
    for page_no, text in _read_pages(path):
        sections, heading = _sections(text, heading)
        for head, para in sections:
            for piece in _chunk(para):
                rows.append((path.name, page_no, f"## {head}\n{piece}" if head else piece))
    if not rows:
        raise ValueError("No readable text found (is it a scanned PDF?)")
    with db() as c:
        c.execute("DELETE FROM chunks WHERE source_file = ?", (path.name,))
        c.executemany("INSERT INTO chunks (source_file, page_number, text) VALUES (?,?,?)", rows)
    return len(rows)


def ingest_all() -> dict:
    files = sorted(p for p in config.DOCS_DIR.iterdir() if p.suffix.lower() in SUPPORTED)
    return {p.name: ingest_file(p) for p in files}


def delete_document(name: str) -> None:
    with db() as c:
        c.execute("DELETE FROM chunks WHERE source_file = ?", (name,))
    (config.DOCS_DIR / name).unlink(missing_ok=True)


def list_documents() -> dict:
    with db() as c:
        rows = c.execute("SELECT source_file, COUNT(*) AS n FROM chunks GROUP BY source_file").fetchall()
    return {r["source_file"]: r["n"] for r in rows}


# ---------- search ----------
def _stem(w: str) -> str:
    """Very light stemmer: contacted -> contact, reviews -> review."""
    for suf in ("ing", "ed"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        return w[:-1]
    return w


def _tokens(text: str):
    return [_stem(w) for w in re.findall(r"[a-z0-9]+", text.lower())
            if w not in ENGLISH_STOP_WORDS and (len(w) > 1 or w.isdigit())]


def _plain(text: str) -> str:
    return text[3:] if text.startswith("## ") else text


def _state():
    with _lock:
        with db() as c:
            r = c.execute("SELECT COUNT(*) AS n, COALESCE(MAX(id), 0) AS m FROM chunks").fetchone()
            sig = (r["n"], r["m"])
            if sig != _index["sig"]:   # KB changed (also by another worker): rebuild
                rows = c.execute("SELECT source_file, page_number, text FROM chunks").fetchall()
                if rows:
                    vec = TfidfVectorizer(tokenizer=_tokens, token_pattern=None,
                                          ngram_range=(1, 2), sublinear_tf=True)
                    texts = [_plain(x["text"]) for x in rows]
                    matrix = vec.fit_transform(texts)
                    vocab = sorted({w for t in texts for w in re.findall(r"[a-z]{4,}", t.lower())})
                    _index.update(sig=sig, vec=vec, matrix=matrix, rows=rows,
                                  vocab=vocab, stems={_stem(w) for w in vocab})
                else:
                    _index.update(sig=sig, vec=None, matrix=None, rows=[], vocab=[], stems=set())
        return dict(_index)


def correct_query(query: str) -> str:
    """Fix small typos by matching unknown words to words that exist in the documents."""
    st = _state()
    if st["vec"] is None:
        return query
    vocab, stems, out = set(st["vocab"]), st["stems"], []
    for w in query.split():
        core = re.sub(r"[^a-z]", "", w.lower())
        if (len(core) >= 4 and core not in vocab and _stem(core) not in stems
                and core not in ENGLISH_STOP_WORDS):
            m = difflib.get_close_matches(core, st["vocab"], n=1, cutoff=0.8)
            out.append(m[0] if m else w)
        else:
            out.append(w)
    return " ".join(out)


def search(query: str, k: int = None):
    """Top chunks for a question: [{source_file, page_number, text, score}]."""
    k = k or config.TOP_K
    st = _state()
    if st["vec"] is None:
        return []
    scores = linear_kernel(st["vec"].transform([query]), st["matrix"]).ravel()
    best = scores.argsort()[::-1][:k]
    return [{"source_file": st["rows"][i]["source_file"], "page_number": st["rows"][i]["page_number"],
             "text": st["rows"][i]["text"], "score": float(scores[i])}
            for i in best if scores[i] >= config.MIN_SCORE]


def score_texts(query: str, texts):
    """Similarity of the question to each text (used to pick the exact sentences)."""
    st = _state()
    if st["vec"] is None or not texts:
        return [0.0] * len(texts)
    return linear_kernel(st["vec"].transform([query]), st["vec"].transform(texts)).ravel().tolist()
