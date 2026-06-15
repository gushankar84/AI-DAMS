"""Hybrid retrieval: parallel candidate generation across signals, fused with
Reciprocal Rank Fusion (TSA §6.3). Cross-encoder reranking is layered on in P4.

    Plan -> parallel candidates (BM25 + dense-text + clip-image + transcript)
         -> fuse (RRF) -> [rerank] -> frame-mapped results
"""
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from . import embed_client, opensearch_store, qdrant_store
from . import constants as C

RRF_K = 60  # standard RRF damping constant


def _safe(fn, *args):
    """Run a candidate-generation call, swallowing failures (a down signal must
    not break the others). Returns None on error."""
    try:
        return fn(*args)
    except Exception:
        return None


def _rrf_merge(ranked_lists: dict[str, list[str]], weights: dict[str, float]) -> dict[str, float]:
    """ranked_lists: signal -> [asset_id ...] in rank order. Returns asset_id -> fused score."""
    fused: dict[str, float] = defaultdict(float)
    for signal, ids in ranked_lists.items():
        w = weights.get(signal, 1.0)
        for rank, asset_id in enumerate(ids):
            fused[asset_id] += w * (1.0 / (RRF_K + rank + 1))
    return fused


def hybrid_search(req) -> tuple[list[str], dict[str, dict]]:
    """Returns (ordered_asset_ids, evidence) where evidence[asset_id] carries
    matched signals, snippet, and timeline hits for response assembly."""
    candidate_limit = max(req.limit * 4, 50)
    ranked: dict[str, list[str]] = {}
    evidence: dict[str, dict] = defaultdict(
        lambda: {"signals": set(), "snippet": None, "timeline": []}
    )

    # Candidate generation runs concurrently (TSA §6.3 "parallel candidates").
    # Independent first wave: BM25, transcript (OpenSearch), and the two query
    # embeddings (HTTP to the model server). Then the two vector searches.
    def _bm25():
        return opensearch_store.search_assets(
            req.q, candidate_limit, req.types, req.department, req.project,
            req.language, req.date_from, req.date_to,
            intent=getattr(req, "intent", None))

    def _transcripts():
        return opensearch_store.search_transcripts(req.q, candidate_limit, req.types) if req.q else []

    with ThreadPoolExecutor(max_workers=4) as pool:
        f_bm = pool.submit(_safe, _bm25)
        f_tr = pool.submit(_safe, _transcripts)
        f_tvec = pool.submit(_safe, embed_client.embed_text, req.q) if req.q else None
        f_ivec = pool.submit(_safe, embed_client.embed_clip_text, req.q) if req.q else None
        tvec = f_tvec.result() if f_tvec else None
        ivec = f_ivec.result() if f_ivec else None
        # second wave: vector searches depend on the embeddings
        # Dense-text relevance floor: the UI dial (req.min_score) overrides the tuned default.
        text_floor = req.min_score if getattr(req, "min_score", None) is not None \
            else C.SCORE_THRESHOLDS.get(C.QDRANT_TEXT)
        f_dense = pool.submit(_safe, qdrant_store.search, C.QDRANT_TEXT, tvec, candidate_limit,
                              req.types, req.department, req.project, text_floor) if tvec else None
        # Images carry several vectors each (whole + person crops), so fetch more
        # rows to ensure enough unique assets survive the per-asset dedup.
        img_fetch = max(candidate_limit * 4, 200)
        f_img = pool.submit(_safe, qdrant_store.search, C.QDRANT_IMAGE, ivec, img_fetch,
                            req.types, req.department, req.project,
                            C.SCORE_THRESHOLDS.get(C.QDRANT_IMAGE)) if ivec else None
        bm = f_bm.result() or []
        tr = f_tr.result() or []
        dense = (f_dense.result() if f_dense else None) or []
        img = (f_img.result() if f_img else None) or []

    # 1) BM25 keyword/metadata
    ranked["keyword"] = [h["asset_id"] for h in bm]
    for h in bm:
        evidence[h["asset_id"]]["signals"].add("keyword")
        # Modality attribution from the source-separated fields: tell the user whether the
        # word was SEEN (caption/tags/OCR) or SAID (transcript) — not just "keyword".
        if h.get("seen"):
            evidence[h["asset_id"]]["signals"].add("seen")
        if h.get("said"):
            evidence[h["asset_id"]]["signals"].add("said")
        if h.get("snippet") and not evidence[h["asset_id"]]["snippet"]:
            evidence[h["asset_id"]]["snippet"] = h["snippet"]
        # NOTE: do NOT feed the BM25 snippet to the reranker — it carries <em>…</em> highlight
        # markup, and the cross-encoder over-scores the emphasized token (rerank "police" vs
        # "Objects: <em>pole</em>…" = 0.148 vs 0.006 for the same text un-marked), manufacturing
        # false positives. rr_texts come from the clean dense/transcript snippets only.

    # 2) Dense semantic text. Short-fragment guard: a dense match whose chunk is a tiny
    #    fragment (single-segment transcript residue, "🎵", "sorry sir") is admitted only if a
    #    query word also appears in it — otherwise its near-centroid vector is a black hole that
    #    clears the cosine floor for anything. Windowed chunks (40+ words) always pass, so real
    #    semantic/cross-lingual recall is untouched.
    qwords = {w for w in re.findall(r"\w+", (req.q or "").lower()) if len(w) >= 2}

    def _dense_ok(h) -> bool:
        snip = h["payload"].get("snippet") or ""
        if len(snip.split()) >= C.MIN_DENSE_WORDS:
            return True
        low = snip.lower()
        return any(w in low for w in qwords)

    dense = [h for h in dense if _dense_ok(h)]
    ranked["semantic"] = [h["asset_id"] for h in dense]
    for h in dense:
        aid = h["asset_id"]
        evidence[aid]["signals"].add("semantic")
        # Keep the asset's best dense cosine — the relevance gate falls back to it when the
        # cross-encoder is not confident (paraphrase/cross-lingual), to keep strong bi-encoder
        # matches while still cutting the marginal-cosine noise band.
        if h.get("score", 0) > evidence[aid].get("dense_cos", 0):
            evidence[aid]["dense_cos"] = h.get("score", 0)
        snip = h["payload"].get("snippet")
        if snip:
            if not evidence[aid]["snippet"]:
                evidence[aid]["snippet"] = snip
            # Collect the asset's top-scoring SEMANTIC segments as rerank candidates, so the
            # cross-encoder judges its best content — not whichever line BM25 stored first.
            rt = evidence[aid].setdefault("rr_texts", [])
            if snip not in rt and len(rt) < 3:
                rt.append(snip)
        # Document chunks carry their PAGE → a clickable "p.N ▶" chip that opens the PDF at
        # that page (the document twin of the video moment-seek).
        page = h["payload"].get("page")
        if page is not None and len(evidence[aid]["timeline"]) < 5:
            if not any(t.get("page") == page for t in evidence[aid]["timeline"]):
                evidence[aid]["timeline"].append({
                    "frame_index": None, "smpte": None, "kind": "page",
                    "label": f"Page {page}", "snippet": (snip or "")[:160] or None, "page": page,
                })

    # 3) Text -> image (SigLIP shared space). Each image has several vectors (whole
    #    frame + person crops); keep the best-scoring one per asset, then apply an
    #    adaptive cut relative to the top match (SigLIP's scale shifts per query).
    if img:
        seen: set[str] = set()
        best = []
        for h in img:  # img is sorted by score desc
            if h["asset_id"] not in seen:
                seen.add(h["asset_id"])
                best.append(h)
        img = best
        top_img = img[0]["score"]
        cut = max(C.IMAGE_ABS_FLOOR, top_img * C.IMAGE_REL_RATIO)
        img = [h for h in img if h["score"] >= cut]
    ranked["image"] = [h["asset_id"] for h in img]
    for h in img:
        aid = h["asset_id"]
        evidence[aid]["signals"].add("image")
        # A video keyframe match: record the matching frame as a seek target and surface
        # that frame's image, so the result shows the exact shot where the match is
        # (not a generic poster). img is deduped to the best-scoring frame per asset.
        pl = h.get("payload") or {}
        if pl.get("frame_index") is not None:
            evidence[aid]["timeline"].append({
                "frame_index": pl.get("frame_index"), "smpte": pl.get("smpte"),
                "kind": "visual", "label": None, "snippet": None,
            })
            if pl.get("frame_uri") and not evidence[aid].get("match_frame_uri"):
                evidence[aid]["match_frame_uri"] = pl["frame_uri"]

    # 4) Transcript / Smart Timeline
    ranked["transcript"] = []
    for h in tr:
        aid = h["asset_id"]
        if aid not in ranked["transcript"]:
            ranked["transcript"].append(aid)
        evidence[aid]["signals"].add("transcript")
        evidence[aid]["timeline"].append({
            "frame_index": h.get("start_frame"), "smpte": h.get("smpte"),
            "kind": "speech", "label": h.get("speaker"), "snippet": h.get("snippet"),
        })
        if h.get("snippet"):
            rt = evidence[aid].setdefault("rr_texts", [])
            if h["snippet"] not in rt and len(rt) < 3:
                rt.append(h["snippet"])
            if not evidence[aid]["snippet"]:
                evidence[aid]["snippet"] = h["snippet"]

    weights = {"keyword": 1.0, "semantic": 1.1, "image": 1.0, "transcript": 1.2}
    # Intent leans the fusion toward the meant modality — SOFT (reorders, never drops).
    intent = getattr(req, "intent", None)
    if intent == "spoken":
        weights.update({"transcript": 2.2, "image": 0.6})
    elif intent == "visual":
        weights.update({"image": 2.0, "semantic": 1.3, "transcript": 0.6})
    elif intent == "written":
        weights.update({"keyword": 1.6, "image": 0.5, "transcript": 0.7})
    fused = _rrf_merge(ranked, weights)
    ordered = sorted(fused.keys(), key=lambda a: fused[a], reverse=True)
    for aid in ordered:
        evidence[aid]["score"] = fused[aid]
        evidence[aid]["signals"] = sorted(evidence[aid]["signals"])
    # DEGRADED = a query was given but an embedding call to the model server FAILED, so the
    # semantic/visual paths silently dropped out and only keyword/BM25 ran. The API surfaces this
    # so the UI can say so — instead of the user seeing results vanish (police 17→0) with no clue.
    degraded = bool(req.q) and (tvec is None or ivec is None)
    return ordered, evidence, degraded
