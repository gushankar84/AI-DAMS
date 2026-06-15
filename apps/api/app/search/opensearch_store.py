"""OpenSearch access — BM25 keyword/metadata candidates + timed transcript search."""
from functools import lru_cache

from opensearchpy import OpenSearch

from ..config import settings
from . import constants as C


@lru_cache
def client() -> OpenSearch:
    http_auth = None
    if settings.opensearch_user:
        http_auth = (settings.opensearch_user, settings.opensearch_password or "")
    return OpenSearch(hosts=[settings.opensearch_url], http_auth=http_auth, timeout=15)


# Custom analyzer: standard tokenizer + lowercase, then (1) word_delimiter_graph so a name
# glued to a digit in a filename ("Stella1" → stella + 1, original kept) is findable by the bare
# name, and (2) kstem — a CONSERVATIVE stemmer that normalises grammatical number/tense
# ("curtains"→curtain, "shirts"→shirt) WITHOUT the edit-distance false positives of fuzzy
# matching (it never merges police/pole/policy). This is the right companion to exact matching:
# match the word and its forms, nothing else.
# SEARCH-TIME synonyms (colour families + object hypernyms + common terms) so a query word
# reaches its kin: "brown" → beige/tan/khaki, "car" → suv/jeep/vehicle, "couch" → sofa. Applied
# ONLY at search time (search_analyzer) — the index stays literal, the QUERY expands. Placed
# AFTER the stemmer so it sees the same singular token forms the index stored; every term here is
# non-plural so the stemmer leaves it unchanged. Bidirectional within a comma group. The ranking
# layer (decompose.color_family + family-aware present/contradicts) is the companion to this.
_SYNONYMS = [
    # colour families
    "brown,beige,tan,khaki,camel,chestnut,mocha,coffee,taupe,sand",
    "grey,gray,silver,charcoal,ash,slate",
    "red,crimson,scarlet,maroon,burgundy,cherry",
    "blue,navy,cobalt,indigo,azure",
    "green,olive,lime,emerald",
    "yellow,gold,golden,mustard,amber",
    "orange,rust,apricot",
    "purple,violet,lavender,mauve,magenta",
    "pink,rose,fuchsia,salmon",
    "white,cream,ivory",
    "black,jet,ebony",
    # object hypernyms (a search for the category finds the specific, and vice-versa)
    "vehicle,car,automobile,auto,suv,jeep,truck,van,lorry",
    "sofa,couch,settee",
    "chair,armchair,seat,stool",
    "laptop,computer,notebook,pc",
    "phone,smartphone,mobile,cellphone,telephone,handset",
    "glasses,spectacles,eyeglasses,goggles",
    "bag,handbag,purse,backpack",
    "kid,child",
    "baby,infant,toddler",
    # scene synonyms
    "beach,seashore,shore,coast,coastline,seaside,oceanfront",
    "forest,jungle,woodland,woods",
    "city,town,urban,metropolis",
    "mountain,hill,peak",
    # activity synonyms (gerunds + base; minimal_english only strips plural -s, so these stay)
    "running,jogging,sprinting,run,jog,sprint",
    "walking,strolling,walk",
    "dancing,dance",
    "speaking,talking,addressing,speech",
    # people synonyms (captions say "male/female adult"; users type man/woman/lady)
    "man,male,gentleman",
    "woman,female,lady",
]
_TEXT = {"type": "text", "analyzer": "dam_text", "search_analyzer": "dam_text_search"}
ASSET_MAPPING = {
    "settings": {
        "analysis": {
            "filter": {
                "dam_wd": {
                    "type": "word_delimiter_graph", "split_on_numerics": True,
                    "preserve_original": True, "generate_word_parts": True,
                    "generate_number_parts": True, "catenate_words": False,
                },
                # minimal_english = plural/possessive removal ONLY ("curtains"→curtain,
                # "shirts"→shirt) — the lightest stemmer, so it never collapses distinct words
                # (police/pole stay separate). Heavier stemmers (porter) would hurt the exact
                # word-sense matching we want.
                "dam_stem": {"type": "stemmer", "language": "minimal_english"},
                "dam_syn": {"type": "synonym", "synonyms": _SYNONYMS},
            },
            "analyzer": {
                # INDEX analyzer — literal (no synonyms): store exactly what's in the asset.
                "dam_text": {"tokenizer": "standard",
                             "filter": ["lowercase", "dam_wd", "flatten_graph", "dam_stem"]},
                # SEARCH analyzer — same pipeline + synonym expansion of the QUERY only. The
                # synonym filter sits right after `lowercase` (OpenSearch can't parse a synonym
                # list through word_delimiter_graph); its expanded tokens then flow through the
                # same word-delimiter + stemmer the index used, so they match the stored forms.
                "dam_text_search": {"tokenizer": "standard",
                                    "filter": ["lowercase", "dam_syn", "dam_wd", "flatten_graph", "dam_stem"]},
            },
        }
    },
    "mappings": {
        "properties": {
            "asset_id": {"type": "keyword"},
            "asset_type": {"type": "keyword"},
            "title": _TEXT,
            "description": _TEXT,
            "body": _TEXT,                     # extracted document text / captions (merged — legacy)
            # Source-separated text so a keyword hit knows its MODALITY ("police" SEEN in a
            # frame vs SAID in speech vs WRITTEN in a doc) — intent disambiguation needs this.
            "visual_text": _TEXT,                # VLM captions + tags + on-image OCR (what is SEEN)
            "spoken_text": _TEXT,                # transcript (what is SAID)
            "summary": _TEXT,                    # one-line asset summary (objects/actions/intent rollup)
            "tags": {"type": "keyword"},
            "labels": {"type": "keyword"},       # object/scene labels
            "entities": _TEXT,                   # named entities
            "department": {"type": "keyword"},
            "project": {"type": "keyword"},
            "language": {"type": "keyword"},
            "created_at": {"type": "date"},
        }
    }
}

TRANSCRIPT_MAPPING = {
    "mappings": {
        "properties": {
            "asset_id": {"type": "keyword"},
            "asset_type": {"type": "keyword"},
            "text": {"type": "text"},
            "speaker": {"type": "keyword"},
            "start_frame": {"type": "long"},
            "end_frame": {"type": "long"},
            "smpte": {"type": "keyword"},
        }
    }
}


def ensure_indices() -> None:
    c = client()
    for name, mapping in ((C.OS_ASSETS, ASSET_MAPPING), (C.OS_TRANSCRIPTS, TRANSCRIPT_MAPPING)):
        if not c.indices.exists(index=name):
            c.indices.create(index=name, body=mapping)


def _filters(types, department, project, language, date_from, date_to) -> list[dict]:
    f: list[dict] = []
    if types:
        f.append({"terms": {"asset_type": types}})
    if department:
        f.append({"term": {"department": department}})
    if project:
        f.append({"term": {"project": project}})
    if language:
        f.append({"term": {"language": language}})
    if date_from or date_to:
        rng: dict = {}
        if date_from:
            rng["gte"] = str(date_from)
        if date_to:
            rng["lte"] = str(date_to)
        f.append({"range": {"created_at": rng}})
    return f


def search_assets(q, limit=50, types=None, department=None, project=None,
                  language=None, date_from=None, date_to=None, intent=None) -> list[dict]:
    c = client()
    # Intent shifts FIELD WEIGHTS (soft — a "wrong" intent reorders, never hides). Default
    # (intent=None) keeps the original fields/boosts so existing ranking is byte-identical.
    if intent == "spoken":
        fields = ["spoken_text^4", "title^2", "tags", "labels", "entities", "description", "body"]
    elif intent == "visual":
        fields = ["visual_text^4", "labels^3", "title^2", "tags^2", "entities", "description", "body"]
    elif intent == "written":
        fields = ["body^3", "title^3", "description^2", "tags", "labels", "entities"]
    else:
        fields = ["title^3", "tags^2", "labels^2", "entities^2", "description", "body"]
    must: list[dict] = []
    if q:
        mm = {
            "query": q,
            "fields": fields,
            "type": "best_fields",
            # Require most query terms to match, so a single hit on one word of a multi-word
            # query ("Saturn" in "a spaceship orbiting Saturn") can't surface an unrelated asset.
            "minimum_should_match": "75%",
        }
        # EXACT by default — the search bar already spell-corrects the query (SymSpell), so
        # fuzzy on top only invents matches ("police"→"pole"/"olive"). Opt-in via search_fuzzy.
        if settings.search_fuzzy:
            mm["fuzziness"] = "AUTO:7,10"   # >=7 chars: 1 edit; >=10: 2; shorter must be exact
            mm["prefix_length"] = 2          # first 2 chars must match before any edit
        must.append({"multi_match": mm})
    else:
        must.append({"match_all": {}})
    body = {
        "size": limit,
        "query": {"bool": {"must": must,
                           "filter": _filters(types, department, project, language, date_from, date_to)}},
        # visual_text/spoken_text highlights ATTRIBUTE the match to a modality ("seen" vs
        # "said") without affecting scoring — require_field_match=False computes them for
        # the query terms even though the fields aren't scored in the default search.
        "highlight": {"fields": {"body": {}, "description": {},
                                 "visual_text": {"require_field_match": False},
                                 "spoken_text": {"require_field_match": False}},
                      "fragment_size": 160, "number_of_fragments": 1},
    }
    res = c.search(index=C.OS_ASSETS, body=body)
    hits = []
    for h in res["hits"]["hits"]:
        snippet = None
        hl = h.get("highlight", {})
        for field in ("body", "description", "visual_text", "spoken_text"):
            if field in hl and hl[field]:
                snippet = hl[field][0]
                break
        hits.append({"asset_id": h["_source"]["asset_id"], "score": float(h["_score"]), "snippet": snippet,
                     "seen": "visual_text" in hl, "said": "spoken_text" in hl})
    return hits


def search_transcripts(q, limit=50, types=None) -> list[dict]:
    """Smart Timeline Search: returns asset + frame-mapped segments matching the query."""
    if not q:
        return []
    c = client()
    body = {
        "size": limit,
        "query": {"bool": {
            "must": [{"match": {"text": {"query": q, "fuzziness": "AUTO", "prefix_length": 2}}}],
            "filter": [{"terms": {"asset_type": types}}] if types else [],
        }},
        "highlight": {"fields": {"text": {}}, "fragment_size": 160, "number_of_fragments": 1},
    }
    res = c.search(index=C.OS_TRANSCRIPTS, body=body)
    out = []
    for h in res["hits"]["hits"]:
        src = h["_source"]
        snippet = (h.get("highlight", {}).get("text") or [src.get("text", "")])[0]
        out.append({
            "asset_id": src["asset_id"],
            "score": float(h["_score"]),
            "start_frame": src.get("start_frame"),
            "smpte": src.get("smpte"),
            "speaker": src.get("speaker"),
            "snippet": snippet,
        })
    return out
