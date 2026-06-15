"""Query decomposition for staged 'main keyword → sub keyword' retrieval, WITH attribute
binding.

Splits a natural-language query into ranked CONCEPTS — the subject plus its qualifiers —
ordered broad→narrow by corpus rarity (IDF). Search then favours / requires results that
satisfy the concepts ("a man HANGING", "a man wearing a GREEN SHIRT").

Attribute binding: "red shirt" is ONE concept, not two. An adjective/colour immediately
followed by a noun is grouped into a phrase, and a result only satisfies it when the two
words sit CLOSE TOGETHER (same caption/segment, within a few tokens) — so "red shirt" matches
"wearing a red t-shirt" but NOT "red saree … gray shirt" or "blue shirt with a red emblem".
Per-segment proximity also fixes the video case (words in different shots don't count as bound).

Deliberately dependency-free (no spaCy/LLM): roles + the attribute list are simple lexicons;
ranking is IDF. An LLM decomposer can later slot in as a fallback for queries this misreads.
"""
from __future__ import annotations

import math
import re

from . import constants as C
from . import opensearch_store

# Function words + generic search filler ("show me a photo of …") that carry no retrieval intent.
_STOP = {
    "a", "an", "the", "of", "with", "and", "or", "to", "in", "on", "at", "is", "are", "be",
    "by", "for", "from", "as", "that", "this", "it", "its", "into", "over", "under", "near",
    "up", "down", "show", "find", "get", "me", "my", "all", "any", "some", "photo", "photos",
    "image", "images", "picture", "pictures", "video", "videos", "clip", "clips", "someone",
    "something", "who", "which", "where", "when", "what", "there", "their", "has", "have",
    "had", "was", "were", "wearing", "holding", "doing", "people", "person",
}
# Attribute words — when one of these is immediately followed by a noun it binds to it
# ("green shirt", "bald head"). Colours + common visual descriptors. Ranking still uses IDF;
# this only governs grouping. A miss just means the word stays a standalone concept.
_ATTRS = {
    # Colours — every family member binds (see _COLOR_FAMILIES), so "khaki shorts" / "navy kurta"
    # group into a bound phrase just like "red shirt" does. Kept in sync with _COLORS below.
    "green", "olive", "lime", "emerald",
    "red", "crimson", "scarlet", "maroon", "burgundy", "cherry",
    "blue", "navy", "cobalt", "indigo", "azure",
    "yellow", "gold", "golden", "mustard", "amber",
    "white", "cream", "ivory", "offwhite",
    "black", "jet", "ebony",
    "brown", "beige", "tan", "khaki", "camel", "chestnut", "mocha", "coffee", "taupe", "sand",
    "grey", "gray", "silver", "charcoal", "ash", "slate",
    "orange", "rust", "apricot",
    "pink", "rose", "fuchsia", "salmon",
    "purple", "violet", "lavender", "mauve", "magenta",
    "saffron",
    # Non-colour visual descriptors.
    "bald", "tall", "short", "old", "young", "elderly", "fat", "thin", "long", "big", "small",
    "dark", "light", "bright", "striped", "checked", "plain", "floral",
}
_PROX = 3   # max token gap for an attribute to count as "bound" to its noun (same segment)

# Synonym groups: when a bound noun belongs to a group, the attribute may bind to ANY member.
# So "red shirt" is satisfied by "red dress", "red kurta", "red top" — the colour must sit on a
# GARMENT, but which garment word is used doesn't matter. Add groups (footwear, vehicles…) as
# needed. A noun in no group only matches itself.
_SYN_GROUPS = [
    {"shirt", "tshirt", "tee", "top", "blouse", "kurta", "kurti", "dress", "gown", "saree",
     "sari", "polo", "jacket", "coat", "blazer", "sweater", "hoodie", "suit", "outfit",
     "clothing", "attire", "frock", "robe", "tunic", "vest", "garment",
     "dupatta", "scarf", "stole", "shawl", "veil"},
]


# Colour FAMILIES: a search for any member matches any other member (bidirectional within a
# family). "brown shorts" must reach "beige shorts"; "khaki" must reach "tan"/"beige". Captions
# use one shade word, queries another — families bridge that gap. A colour in no family is its
# own singleton (see color_family). Keep _COLORS (below) ⊇ the union of every family so the
# binding/contradiction logic recognises every shade word as a colour.
_COLOR_FAMILIES = [
    {"brown", "beige", "tan", "khaki", "camel", "chestnut", "mocha", "coffee", "taupe", "sand"},
    {"grey", "gray", "silver", "charcoal", "ash", "slate"},
    {"red", "crimson", "scarlet", "maroon", "burgundy", "cherry"},
    {"blue", "navy", "cobalt", "indigo", "azure"},
    {"green", "olive", "lime", "emerald"},
    {"yellow", "gold", "golden", "mustard", "amber"},
    {"orange", "rust", "apricot"},
    {"purple", "violet", "lavender", "mauve", "magenta"},
    {"pink", "rose", "fuchsia", "salmon"},
    {"white", "cream", "ivory", "offwhite", "off-white"},
    {"black", "jet", "ebony"},
]


def color_family(word: str) -> set[str]:
    """The full colour family containing `word` (incl. `word`), or {word} if it's in no family.
    Lowercased + None-safe so callers (search-side query expansion) can pass raw tokens."""
    if not word:
        return set()
    w = word.lower()
    for fam in _COLOR_FAMILIES:
        if w in fam:
            return fam
    return {w}


# Every recognised colour word = the union of all families. Used by the binding/contradiction
# logic to decide "is this token a colour?", so it must include every shade above.
_COLORS = set().union(*_COLOR_FAMILIES) | {"saffron"}


def _syns(noun: str) -> set[str]:
    """The noun plus its synonym group (e.g. shirt → all garments). {noun} if it's in no group."""
    for g in _SYN_GROUPS:
        if noun in g:
            return g
    return {noun}

_df_cache: dict[str, int] = {}
_total_cache: dict[str, int] = {}
_WORD = re.compile(r"[a-zA-Zऀ-ॿ]+")   # Latin + Devanagari


def _tokens(q: str) -> list[str]:
    """Query content tokens (stopwords + 1-char dropped). Order preserved for binding."""
    return [w for w in _WORD.findall(q.lower()) if len(w) > 1 and w not in _STOP]


def _seg_tokens(text: str) -> list[str]:
    """Ordered tokens of ONE result segment (keep everything incl. 1-char, for proximity)."""
    return _WORD.findall(text.lower())


def _role(term: str) -> str:
    if term in _ATTRS:
        return "attribute"
    if term.endswith("ing") or term.endswith("ed"):
        return "action"
    return "subject"


def _total_assets() -> int:
    if "n" not in _total_cache:
        try:
            _total_cache["n"] = int(opensearch_store.client().count(index=C.OS_ASSETS)["count"]) or 1
        except Exception:
            _total_cache["n"] = 1
    return _total_cache["n"]


def _df(term: str) -> int:
    """How many assets contain `term` (document frequency), cached per process."""
    if term not in _df_cache:
        try:
            r = opensearch_store.client().count(
                index=C.OS_ASSETS, body={"query": {"match": {"body": term}}})
            _df_cache[term] = int(r.get("count", 0))
        except Exception:
            _df_cache[term] = 0
    return _df_cache[term]


def _df_phrase(words: list[str]) -> int:
    """How many assets contain the words in proximity (match_phrase with slop) — the right
    rarity for a BOUND concept like 'green shirt'."""
    key = "~".join(words)
    if key not in _df_cache:
        try:
            r = opensearch_store.client().count(index=C.OS_ASSETS, body={
                "query": {"match_phrase": {"body": {"query": " ".join(words), "slop": _PROX}}}})
            _df_cache[key] = int(r.get("count", 0))
        except Exception:
            _df_cache[key] = 0
    return _df_cache[key]


def decompose(query: str) -> list[dict]:
    """Concepts ordered MAIN (broad) → SUB (rare). Each: {term, words, role, df, idf,
    requirable}. `words` is 1 token, or 2 for a bound attribute+noun ("green shirt").
    `requirable` = every word exists somewhere in the corpus (so it CAN be a hard filter;
    cross-lingual / never-seen words are not requirable). Returns [] when there's nothing to
    enforce — a single bare word with no binding (falls back to normal fused ranking)."""
    terms = list(dict.fromkeys(_tokens(query)))   # dedupe, preserve order
    # Group an attribute immediately followed by a (non-attribute) noun into one phrase.
    groups: list[list[str]] = []
    i = 0
    while i < len(terms):
        if terms[i] in _ATTRS and i + 1 < len(terms) and terms[i + 1] not in _ATTRS:
            groups.append([terms[i], terms[i + 1]])
            i += 2
        else:
            groups.append([terms[i]])
            i += 1
    # Nothing to enforce if it's a single bare word (no second concept, no binding).
    if len(groups) < 2 and not any(len(g) > 1 for g in groups):
        return []
    n = _total_assets()
    out = []
    for g in groups:
        word_dfs = [_df(w) for w in g]
        df = _df_phrase(g) if len(g) == 2 else word_dfs[0]
        out.append({
            "term": " ".join(g), "words": g,
            "role": "attribute+noun" if len(g) == 2 else _role(g[0]),
            "df": df, "idf": math.log((n + 1) / (df + 1)) + 1.0,
            "requirable": all(d > 0 for d in word_dfs),   # all words exist → can hard-require
            # For a bound phrase, the noun may also be any garment-synonym ("red shirt" ⊇ "red dress").
            "noun_syns": _syns(g[1]) if len(g) == 2 else None,
        })
    out.sort(key=lambda c: c["idf"])   # broadest concept first = the retrieval anchor
    return out


def present(concept: dict, segments: list[list[str]]) -> bool:
    """Is the concept satisfied by a result's text? `segments` = ordered token lists, one per
    caption/snippet/segment. Single word → appears in any segment. Bound phrase → its words
    appear WITHIN `_PROX` tokens of each other IN THE SAME segment (attribute bound to noun,
    not merely co-present, and not split across video shots)."""
    words = concept["words"]
    if len(words) == 1:
        w = words[0]
        return any(w in seg for seg in segments)
    a = words[0]                                  # attribute
    # A colour attribute is satisfied by ANY same-family shade ("brown" by "beige"/"tan"/"khaki"),
    # so the binding survives caption-vs-query shade mismatch. Non-colour attrs match exactly.
    attrs = color_family(a) if a in _COLORS else {a}
    nouns = concept.get("noun_syns") or {words[1]}  # noun + garment synonyms
    for seg in segments:
        pa = [k for k, t in enumerate(seg) if t in attrs]
        if not pa:
            continue
        pb = [k for k, t in enumerate(seg) if t in nouns]
        # DIRECTIONAL: the attribute must come BEFORE the noun, within _PROX tokens —
        # "red [polo] shirt" binds (so does "red dress"), but "shirt with a red emblem" (red
        # AFTER the garment) does not — a blue shirt with a red emblem is not a "red shirt".
        if any(0 < j - i <= _PROX for i in pa for j in pb):
            return True
    return False


def contradicts(concept: dict, segments: list[list[str]]) -> bool:
    """True if a result binds the concept's NOUN to a DIFFERENT colour than the query asked —
    e.g. for "red dupatta", a "green dupatta" (dupatta bound to green). Used to prune the
    no-exact-match fallback so we never show the wrong colour, while still allowing results
    whose garment is the right colour or has no stated colour ("white shirt" survives)."""
    if len(concept["words"]) != 2:
        return False
    a = concept["words"][0]
    if a not in _COLORS:
        return False
    nouns = concept.get("noun_syns") or {concept["words"][1]}
    # FAMILY-AWARE: only a colour in a DIFFERENT family contradicts. "brown" vs "beige" share the
    # brown family → not a contradiction (present() already binds them); "red" vs "green" differ →
    # contradiction. This is what stops "brown dupatta" from being pruned by a "beige dupatta".
    fam = color_family(a)
    for seg in segments:
        for k, t in enumerate(seg):
            if t not in nouns:
                continue
            # Take the NEAREST colour in the _PROX window before the noun as THIS garment's colour
            # — so in "white shirt, beige shorts" the shorts' colour is "beige" (same family as
            # brown → no contradiction), not the "white" that actually binds the shirt. Scanning
            # the whole window for any off-family colour would wrongly flag the shirt's colour
            # against the shorts.
            for j in range(k - 1, max(0, k - _PROX) - 1, -1):   # nearest colour first
                if seg[j] in _COLORS:
                    if seg[j] not in fam:
                        return True   # garment's own colour is a DIFFERENT-family colour
                    break             # nearest colour is same-family → this garment is fine
    return False


def coverage(concepts: list[dict], segments: list[list[str]]) -> float:
    """IDF-weighted fraction of concepts satisfied (0..1). Rare/bound concepts pull harder."""
    if not concepts:
        return 1.0
    total = sum(c["idf"] for c in concepts) or 1.0
    hit = sum(c["idf"] for c in concepts if present(c, segments))
    return hit / total
