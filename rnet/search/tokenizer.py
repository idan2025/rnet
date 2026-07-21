"""Simple text tokenizer + term frequency for the search index.

Good enough for a decentralized, low-bandwidth index: lowercase, split on
non-alphanumeric, drop a small stopword set and 1-char tokens, light plural
normalization. No external NLP deps so it runs on constrained nodes.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Iterable, List

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Tiny English stopword set; kept small on purpose.
_STOPWORDS = frozenset(
    "a an and are as at be by for from has have in is it its of on that the to "
    "was were will with this these those they their them he she his her you your "
    "we our i but or not no".split()
)


def normalize(token: str) -> str:
    t = token.lower()
    # light plural / gerund normalization
    if len(t) > 4 and t.endswith("ies"):
        t = t[:-3] + "y"
    elif len(t) > 4 and t.endswith("ing"):
        t = t[:-3]
    elif len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        t = t[:-1]
    return t


def tokenize(text: str) -> List[str]:
    out = []
    for raw in _TOKEN_RE.findall(text.lower()):
        if len(raw) < 2:
            continue
        t = normalize(raw)
        if not t or t in _STOPWORDS or len(t) < 2:
            continue
        out.append(t)
    return out


def term_freqs(text: str) -> dict:
    return dict(Counter(tokenize(text)))