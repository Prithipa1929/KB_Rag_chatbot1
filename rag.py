"""Question answering. Every answer is a bold title + bullet points, with only what was asked."""
import difflib
import json
import logging
import re
import urllib.request

from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

import config
import kb

log = logging.getLogger(__name__)

NOT_FOUND_MD = ("**Not available in KB**\n"
                "- I could not find this in the company documents.\n"
                "- Please contact the support team.")
LLM_NOT_FOUND = "NOT_FOUND"
MIN_SENT_SCORE = 0.08
MAX_BULLETS = 5

PROMPT = """You are the official assistant for {company}. Use ONLY the context below.
Answer ONLY what the question asks. Do not add extra or related information.
Format rules (strict):
- First line: a short bold title, like **Notice period**
- Then bullet points only. Each line starts with "- ". No paragraphs.
- Keep each bullet short and copy numbers/names exactly as in the context.
If the answer is not in the context, reply exactly: {nf}
Reply in the same language the user used.

Context:
{context}

Question: {question}
Answer:"""


# ---------------- small talk (no search needed) ----------------
_GREET = {"hi", "hii", "hiii", "hello", "helo", "hey", "hola", "vanakkam", "vanakam", "namaste", "yo"}
_THANKS = {"thanks", "thank", "thankyou", "thx", "nandri", "nanri", "tq", "ty"}
_BYE = {"bye", "goodbye", "tata", "cya"}


def _close(word, target, cutoff=0.75):
    return bool(difflib.get_close_matches(word, [target], cutoff=cutoff))


def smalltalk(question: str):
    toks = re.findall(r"[a-z']+", question.lower())
    if not toks:
        return None
    text = " ".join(toks)
    c = config.COMPANY_NAME
    if len(toks) <= 6:
        if toks[0] in _GREET or text.startswith(("good morning", "good afternoon", "good evening")):
            return (f"**Hello!**\n- I am the {c} assistant.\n"
                    "- Ask me about company policies, products or processes.\n"
                    "- Example: What is the notice period?")
        if toks[0] in _THANKS or text.startswith("thank you"):
            return "**You are welcome!**\n- Ask me anything else about the company documents."
        if toks[0] in _BYE or text.startswith("good night"):
            return "**Goodbye!**\n- Come back any time you have a question."
        if text in ("how are you", "how are u", "how r u", "how are you doing"):
            return "**I am doing well.**\n- How can I help you today?"
        if text in ("who are you", "what are you", "what can you do", "help", "what do you do"):
            return (f"**About this assistant**\n- I answer questions from the {c} documents.\n"
                    "- I show the source file and page for each answer.")
    if len(toks) <= 8 and any(_close(t, "company") or _close(t, "organization") for t in toks) \
            and any(t == "called" or _close(t, "name", 0.8) for t in toks):
        return f"**Company name**\n- {c}"
    return None


# ---------------- helpers ----------------
def _label(h):
    return f"{h['source_file']} (p.{h['page_number']})" if h["page_number"] else h["source_file"]


def _parse_chunk(text: str):
    if text.startswith("## "):
        head, _, body = text[3:].partition("\n")
        return head.strip(), body
    return "", text


def _sentences(body: str):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"(])", body) if s.strip()]


def _shorten(s: str, limit=280):
    if len(s) <= limit:
        return s
    return s[:limit].rsplit(" ", 1)[0] + "…"


def _query_title(question: str):
    skip = ENGLISH_STOP_WORDS | {"tell", "please", "give", "show", "explain", "know", "want"}
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'-]*", question) if w.lower() not in skip]
    return " ".join(words).title() or "Answer"


def _gemini(prompt: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.LLM_MODEL}:generateContent"
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}],
                       "generationConfig": {"temperature": 0.2}}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json", "x-goog-api-key": config.GOOGLE_API_KEY})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()


# ---------------- question types: keep only the kind of sentence that was asked for ----------------
_TIME = re.compile(r"\d{1,2}:\d{2}|\b[ap]\.?m\b|\bhours?\b|working days", re.I)
_PERSON = re.compile(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b")
_NOT_A_NAME = ENGLISH_STOP_WORDS | {"new", "every", "each", "all", "any"}


def _has_person(s: str) -> bool:
    return any(m.start() > 0 or m.group().split()[0].lower() not in _NOT_A_NAME
               for m in _PERSON.finditer(s))


_INTENTS = [
    ({"name", "who", "whom"}, _has_person),                                    # "hr name?"
    ({"email", "mail", "phone", "mobile", "extension", "call"},                # "hr email?"
     lambda s: bool(re.search(r"@|\bextension\b|\bphone\b|\bmobile\b|\d{5,}", s, re.I))),
    ({"contact", "reach"},                                                     # "can i contact hr?"
     lambda s: bool(re.search(r"@|\bextension\b|\bphone\b|\bcontact", s, re.I)) or bool(_TIME.search(s))),
    ({"when", "time", "timing", "timings", "hours", "open"}, lambda s: bool(_TIME.search(s))),
    ({"limit", "amount", "cost", "fee", "price", "salary", "many", "much"}, lambda s: bool(re.search(r"\d", s))),
]


def _intent_filter(question: str, cands):
    words = set(re.findall(r"[a-z]+", question.lower()))
    for trigger, matches in _INTENTS:
        if words & trigger:
            hit = [c for c in cands if matches(c["text"]) and c["sent_score"] >= MIN_SENT_SCORE]
            if hit:                      # only narrow down when something matches
                cands = hit
    return cands


def _extractive(question: str, hits):
    """Pick only the sentences that match the question; group them under their section heading."""
    cands, seen = [], set()
    for h in hits:
        head, body = _parse_chunk(h["text"])
        for s in _sentences(body):
            key = re.sub(r"\W+", " ", s.lower()).strip()
            if len(key) < 15 or key in seen:
                continue
            seen.add(key)
            cands.append({"head": head, "text": s, "hit": h})
    if not cands:
        return None
    s_scores = kb.score_texts(question, [c["text"] for c in cands])
    h_scores = kb.score_texts(question, [c["head"] or " " for c in cands])
    for c, s, hs in zip(cands, s_scores, h_scores):
        c["score"] = s + 0.5 * hs if c["head"] else s
        c["sent_score"] = s
    cands = _intent_filter(question, cands)
    best = max(c["sent_score"] for c in cands)
    if best < MIN_SENT_SCORE:
        return None
    top = max(cands, key=lambda c: c["sent_score"])
    keep = [c for c in cands
            if (c["head"] == top["head"] and c["sent_score"] >= max(MIN_SENT_SCORE, 0.5 * best))  # same section
            or c["sent_score"] >= 0.9 * best]                                                      # near-tie elsewhere
    keep = sorted(keep, key=lambda c: -c["score"])[:MAX_BULLETS]

    groups = {}                                   # heading -> items, best group first
    for c in keep:
        groups.setdefault(c["head"], []).append(c)
    ordered = sorted(groups.items(), key=lambda kv: -max(x["score"] for x in kv[1]))[:2]

    blocks, used = [], []
    for head, items in ordered:
        items.sort(key=lambda c: cands.index(c))  # keep document order inside a group
        title = head or _query_title(question)
        blocks.append(f"**{title}**\n" + "\n".join("- " + _shorten(c["text"]) for c in items))
        used += [c["hit"] for c in items]
    return "\n\n".join(blocks), sorted({_label(h) for h in used})


# ---------------- main entry ----------------
def ask(question: str):
    """Returns (answer, sources, found). Answer is markdown-lite: **title** + '- bullets'."""
    talk = smalltalk(question)
    if talk:
        return talk, [], True

    q = kb.correct_query(question)
    hits = kb.search(q)
    if not hits:
        return NOT_FOUND_MD, [], False

    if config.GOOGLE_API_KEY:
        try:
            context = "\n\n".join(h["text"].replace("## ", "Section: ", 1) for h in hits)
            answer = _gemini(PROMPT.format(company=config.COMPANY_NAME, nf=LLM_NOT_FOUND,
                                           context=context, question=question))
            if LLM_NOT_FOUND in answer:
                return NOT_FOUND_MD, [], False
            return answer, sorted({_label(h) for h in hits}), True
        except Exception:
            log.exception("Gemini call failed; falling back to extractive answer")

    result = _extractive(q, hits)
    if not result:
        return NOT_FOUND_MD, [], False
    return result[0], result[1], True
