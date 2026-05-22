import os
import re
from pathlib import Path
from typing import List
import math

# Simple, dependency-free RAG (retrieval-augmented generation) helper
# Loads the local Bear Trap guide and returns the most relevant paragraphs
# for a given question using a bag-of-words overlap scoring.

BASE_DIR = Path(__file__).parent.parent.parent
BEARTRAP_PATH = BASE_DIR / "data" / "wos" / "beartrap.txt"


def _load_text() -> str:
    try:
        with BEARTRAP_PATH.open("r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def _split_paragraphs(text: str) -> List[str]:
    # Split on double newlines and strip
    parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return parts


_RAW_TEXT = _load_text()
_PARAGRAPHS = _split_paragraphs(_RAW_TEXT)


# Minimal stopword set to reduce noise
_STOPWORDS = {
    'the','and','is','in','to','of','a','you','for','with','on','it','that','this',
    'as','are','be','by','or','an','from','your','will','can','have','has','was',
}


def _tokenize(s: str) -> List[str]:
    s = s.lower()
    # keep words and numbers
    tokens = re.findall(r"[a-z0-9']+", s)
    tokens = [t for t in tokens if t not in _STOPWORDS]
    return tokens


# Preprocess: split into sentences and compute token sets
_SENTENCES = []  # list of (p_idx, s_idx, sentence_text)
for p_idx, para in enumerate(_PARAGRAPHS):
    parts = re.split(r'(?<=[\.\?\!])\s+', para)
    for s_idx, sent in enumerate(parts):
        s = sent.strip()
        if s:
            _SENTENCES.append((p_idx, s_idx, s))

# Build token sets and document frequencies for IDF
_SENT_TOKENS = []
_DF = {}
for (_, _, sent) in _SENTENCES:
    toks = set(_tokenize(sent))
    _SENT_TOKENS.append(toks)
    for t in toks:
        _DF[t] = _DF.get(t, 0) + 1

_N_SENT = max(1, len(_SENT_TOKENS))
_IDF = {t: (0.0 if _DF.get(t, 0) == 0 else (math.log(_N_SENT / _DF[t]) + 1.0)) for t in _DF}

# Minimal stopword set to reduce noise
_STOPWORDS = {
    'the','and','is','in','to','of','a','you','for','with','on','it','that','this',
    'as','are','be','by','or','an','from','your','will','can','have','has','was',
}


def _tokenize(s: str) -> List[str]:
    s = s.lower()
    # keep words and numbers
    tokens = re.findall(r"[a-z0-9']+", s)
    tokens = [t for t in tokens if t not in _STOPWORDS]
    return tokens


def is_beartrap_question(question: str) -> bool:
    if not question:
        return False
    q = question.lower()
    keywords = ["bear", "bear trap", "bear hunt", "bearhunt", "beartrap", "trap enhancement", "trap"]
    # match if any of the keywords appear, but avoid false positives for unrelated 'bear' mentions
    for kw in keywords:
        if kw in q:
            return True
    return False


def answer_beartrap_question(question: str, top_k: int = 3) -> str:
    """
    Return a short, retrieval-based answer composed of the top_k relevant
    paragraphs from the loaded guide. This is intentionally deterministic
    and dependency-free so it works offline.
    """
    # If guide missing
    if not _PARAGRAPHS:
        return "Bear Trap guide not available."

    q_tokens = set(_tokenize(question))

    # Intent map: quick canonical answers for frequently asked topics
    intent_map = {
        'cooldown': ['cooldown', '46 hours', '2 natural days', 'personal cooldown'],
        'enhancement': ['enhance', 'explosive arrowhead', 'arrowheads', 'trap enhancement', 'enhancement level'],
        'requirements': ['build', 'requirement', 'alliance level', 'hq', 'hunting trap'],
        'rewards': ['reward', 'rewards', 'essence', 'alliance tokens', 'personal rewards'],
        'participate': ['participate', 'shield', 'join', 'two bear hunts', 'two traps'],
        'heroes': ['hero', 'joiner', 'rally leader', 'best heroes', 'expedition skill'],
        'march speed': ['march speed', 'speed', 'seconds'],
    }

    q_lower = question.lower()
    # Check intent map first for direct matches
    for intent, kws in intent_map.items():
        for kw in kws:
            if kw in q_lower:
                # Find best sentence containing any of these keywords
                best = None
                for idx, (p_idx, s_idx, sent) in enumerate(_SENTENCES):
                    if kw in sent.lower():
                        best = sent
                        break
                if best:
                    answer = f"Answer (topic: {intent}): {best}\n\n(Ask follow-up for more details.)"
                    return answer

    # Otherwise compute TF-IDF-like score per sentence (sum of IDF of matched tokens)
    scores = []
    for i, (_p_idx, _s_idx, sent) in enumerate(_SENTENCES):
        s_tokens = _SENT_TOKENS[i]
        score = 0.0
        for t in q_tokens:
            if t in s_tokens:
                score += _IDF.get(t, 0.0)
        scores.append((score, i))

    # Pick top sentences
    scores.sort(key=lambda x: (-x[0], x[1]))
    top = [idx for score, idx in scores if score > 0][:top_k]
    if not top:
        # fallback: return first helpful sentences (not raw dump)
        top = list(range(min(top_k, len(_SENTENCES))))

    selected = [ _SENTENCES[i][2] for i in top ]

    # Synthesize: lead with the most relevant sentence then add supporting ones
    synthesized = selected[0]
    if len(selected) > 1:
        synthesized += " " + " ".join(selected[1:])

    # Trim and add hint
    if len(synthesized) > 1800:
        synthesized = synthesized[:1790].rsplit(' ', 1)[0] + "..."
    synthesized = "Short answer based on the Bear Hunt guide:\n\n" + synthesized
    synthesized += "\n\n(If you want more context say 'more' or ask a follow-up question.)"
    return synthesized
