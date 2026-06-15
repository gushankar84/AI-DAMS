"""Universal Search (BRD §5.2) — one box across documents, images, audio, video.

Runs hybrid candidate generation + RRF fusion, then hydrates the top page from
Postgres and attaches frame-mapped timeline hits for media.
"""
import asyncio
import re
import time
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Asset, Marker
from ..schemas import SearchHit, SearchRequest, SearchResponse, TimelineHit
from ..search import decompose, embed_client, qdrant_store
from ..search import constants as C
from ..search.constants import QDRANT_FACE
from ..search.hybrid import hybrid_search
from ..security import CurrentUser
from ..storage import put_object
from .. import audit

router = APIRouter(prefix="/api/search", tags=["search"])

# Language words → asset language codes, for language-aware query narrowing.
_LANG_WORDS = {"hindi": "hi", "tamil": "ta", "telugu": "te", "arabic": "ar", "english": "en",
               "kannada": "kn", "malayalam": "ml", "urdu": "ur", "bengali": "bn", "punjabi": "pa"}

# Modality-intent cues: explicit phrasing that says WHERE the user means to look. The cue
# phrase is STRIPPED from the retrieval query (it's meta, not content: "talks about police"
# should search "police" in speech) and the intent leans ranking — soft, never excluding.
_INTENT_CUES: list[tuple[str, str]] = [
    (r"\b(?:talks?|talking|speaks?|speaking)\s+(?:about|of)\b", "spoken"),
    (r"\b(?:says?|saying|said|mentions?|mentioned|quotes?|spoken about)\b", "spoken"),
    (r"\bspeech (?:about|on)\b", "spoken"),
    (r"\b(?:wearing|dressed in)\b", "visual"),
    (r"\b(?:shows?|showing|appears?|appearing|visible|looks? like|scene of|photo of|picture of|image of|frames? (?:with|of))\b", "visual"),
    (r"\b(?:written|writes?|signboard|sign says|text says|titled|caption says|document about)\b", "written"),
]


def _detect_intent(q: str) -> tuple[str, str | None]:
    """(cleaned_query, intent|None). Strips ONLY the cue words that name the modality
    ("talks about X" → "X", spoken). 'wearing' is kept in the query — it's also content."""
    low = q.lower()
    for pat, intent in _INTENT_CUES:
        if re.search(pat, low):
            cleaned = q
            if intent in ("spoken", "written"):    # meta phrases — remove from retrieval text
                cleaned = re.sub(pat, " ", q, flags=re.IGNORECASE)
                cleaned = re.sub(r"\s{2,}", " ", cleaned).strip() or q
            return cleaned, intent
    return q, None

# Corpus vocabulary for typo correction — built once from the VLM captions + OCR text,
# so corrections target words that ACTUALLY exist in the library ("beerd"→"beard") and
# domain terms ("saree", "dupatta", which appear in captions) are never mangled. Common
# words ("with", "man") are in the captions too, so they're protected automatically.
_SPELLER = None  # cached SymSpell (None=unbuilt, False=unavailable)


async def _speller(db: AsyncSession):
    """SymSpell built from an 82k English dictionary PLUS the library's own vocabulary
    (caption/OCR words) added at very high frequency. Effect: common English words are
    known (not corrected), domain terms ('saree','dupatta') are preserved, and real
    library words win as correction targets ('beerd'->'beard', 'grren'->'green'). Built
    once and cached; degrades to no-op if symspell is unavailable."""
    global _SPELLER
    if _SPELLER is None:
        try:
            import os
            import symspellpy
            from symspellpy import SymSpell
            sp = SymSpell(max_dictionary_edit_distance=2)
            sp.load_dictionary(os.path.join(os.path.dirname(symspellpy.__file__),
                                            "frequency_dictionary_en_82_765.txt"), 0, 1)
            labels = (await db.execute(select(Marker.label).where(
                Marker.kind.in_(["scene", "ocr"]), Marker.label.is_not(None)))).scalars().all()
            words: set[str] = set()
            for lbl in labels:
                words.update(re.findall(r"[a-z]{4,}", (lbl or "").lower()))
            # Curated visual-search attributes — common targets users type (and mistype)
            # that the auto-captions may phrase differently, so they win over base-dict
            # neighbours ("beerd"->"beard", not "beer"). High frequency = preferred target.
            words.update({
                "beard", "bald", "mustache", "moustache", "glasses", "spectacles", "goggles",
                "saree", "sari", "dupatta", "kurta", "lehenga", "turban", "scarf", "uniform",
                "suit", "shirt", "tshirt", "blouse", "dress", "jacket", "saree", "necklace",
                "blue", "green", "red", "yellow", "black", "white", "orange", "purple", "pink",
                "woman", "women", "child", "person", "people", "building", "vehicle",
            })
            # The library's OWN proper nouns — asset titles/filenames + named people — so a name
            # the user types ("Jagan", "Stella", "Raghupathy") is a KNOWN word and is never
            # "corrected" into a dictionary neighbour ("Jagan" → "japan"), which silently
            # searched the wrong term and returned zero of the person's photos.
            from ..models import Person
            _EXT = {"jpg", "jpeg", "jpe", "png", "gif", "webp", "mp4", "mov", "wav", "mp3",
                    "pdf", "docx", "image"}
            srcs = [v for row in (await db.execute(
                        select(Asset.title, Asset.filename))).all() for v in row]
            srcs += (await db.execute(select(Person.display_name).where(
                        Person.display_name.is_not(None)))).scalars().all()
            for src in srcs:
                for w in re.findall(r"[a-z]{3,}", (src or "").lower()):
                    if w not in _EXT:
                        words.add(w)
            for w in words:
                sp.create_dictionary_entry(w, 10 ** 10)
            _SPELLER = sp
        except Exception:
            _SPELLER = False
    return _SPELLER or None


def _spell_correct(q: str, sp) -> str:
    """Fix obvious typos word-by-word. In-vocab words resolve to themselves (no change);
    short (<4) and non-alpha tokens are left alone."""
    if not sp:
        return q
    from symspellpy import Verbosity
    out = []
    for w in q.split():
        lw = w.lower()
        if len(lw) >= 4 and lw.isalpha():
            sug = sp.lookup(lw, Verbosity.TOP, max_edit_distance=2)
            out.append(sug[0].term if (sug and sug[0].term != lw) else w)
        else:
            out.append(w)
    return " ".join(out)


@router.post("", response_model=SearchResponse)
async def search(req: SearchRequest, user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):
    t0 = time.perf_counter()
    # Typo-tolerance (query layer): map misspelled words to the library's own vocabulary
    # ("a man with a beerd" -> "a man with a beard") so sloppy input still finds the right
    # asset, WITHOUT loosening the relevance gate. Domain terms (saree/dupatta) are in the
    # captions so they're never mangled; corrections target real searchable words.
    if req.q:
        corrected = _spell_correct(req.q, await _speller(db))
        if corrected != req.q:
            req.q = corrected
    # Modality intent: explicit (UI chip) wins; otherwise auto-detect from phrasing
    # ("talks about police" → spoken, cue stripped so retrieval searches "police").
    if req.q and not req.intent:
        cleaned, detected = _detect_intent(req.q)
        if detected:
            req.q = cleaned
            req.intent = detected
    # Query decomposition: split "a man hanging" into ranked concepts (main=broad subject,
    # sub=rare qualifier) so the re-rank can favour results that satisfy ALL of them, not just
    # one. Cheap: returns [] instantly for single-concept queries (no corpus lookups then).
    concepts = await asyncio.to_thread(decompose.decompose, req.q) if req.q else []
    # Content tokens of the query (stopwords dropped). Used for the LITERAL-match gate bypass
    # below: the cross-encoder is unreliable on bare keyword queries ("man" → 0.011 even on a
    # caption that literally says "the man wears…"), so an exact full-term match must survive.
    qtokens = set(decompose._tokens(req.q)) if req.q else set()
    # hybrid_search is blocking (sync HTTP to the model server + vector/keyword
    # stores). Run it in a worker thread so it doesn't block the event loop —
    # otherwise concurrent searches serialize and throughput collapses to ~2 req/s.
    ordered, evidence, degraded = await asyncio.to_thread(hybrid_search, req)
    total = len(ordered)

    # Language-aware narrowing: if the query names a language ("hindi song"), drop hits
    # whose asset is in a DIFFERENT detected language. Language-less assets (images, docs,
    # silent clips) are kept. Stops Tamil/Telugu/Arabic clips matching "hindi …".
    want_lang = next((c for w, c in _LANG_WORDS.items()
                      if req.q and re.search(rf"\b{w}\b", req.q.lower())), None)

    # Hydrate a candidate pool (larger than the page when reranking) preserving fused order.
    do_rerank = req.rerank and bool(req.q) and req.sort == "relevance"
    # Long, descriptive queries are noise-prone via the bi-encoder; they take the lenient
    # rerank path AND the LLM relevance refine below. Short queries stay strict + fast.
    lenient = len(req.q.split()) >= 3
    pool_size = max(req.offset + req.limit, 30 if do_rerank else req.offset + req.limit)
    pool_ids = ordered[:pool_size]

    hits: list[SearchHit] = []
    if pool_ids:
        rows = (await db.execute(select(Asset).where(
            Asset.id.in_(pool_ids), Asset.deleted_at.is_(None)))).scalars().all()
        by_id = {a.id: a for a in rows}
        for aid in pool_ids:
            a = by_id.get(aid)
            if not a:
                continue
            if want_lang and a.language and a.language != want_lang:
                continue  # language-qualified query → skip other-language assets
            # Date range filter applied to ALL signals here (the vector stores don't
            # index created_at, so BM25-only filtering would let semantic/image hits leak).
            cdate = a.created_at.date() if a.created_at else None
            if req.date_from and (cdate is None or cdate < req.date_from):
                continue
            if req.date_to and (cdate is None or cdate > req.date_to):
                continue
            ev = evidence.get(aid, {})
            timeline = [TimelineHit(**t) for t in ev.get("timeline", [])][:10]
            # For a video matched visually, show the matching keyframe (the shot where the
            # match is) instead of the generic poster.
            thumb = ev.get("match_frame_uri") or a.thumbnail_uri
            hits.append(SearchHit(
                asset_id=a.id, type=a.type, title=a.title, filename=a.filename,
                thumbnail_uri=thumb, score=round(ev.get("score", 0.0), 6),
                matched_signals=ev.get("signals", []), snippet=ev.get("snippet"),
                timeline=timeline, created_at=a.created_at,
            ))

    # Attach the VLM scene caption so visually-matched (text-less) cards aren't bare —
    # the user sees "what the system sees". One batched query over the hydrated page.
    if hits:
        cap_rows = (await db.execute(select(Marker.asset_id, Marker.label).where(
            Marker.asset_id.in_([h.asset_id for h in hits]), Marker.kind == "scene"))).all()
        cap_by_id: dict[str, str] = {}
        for aid, label in cap_rows:
            if label and aid not in cap_by_id:
                cap_by_id[aid] = label
        for h in hits:
            h.caption = cap_by_id.get(h.asset_id)

    # Per-hit text, keyed by asset_id (survives the rerank reorder). Kept as a LIST of ordered
    # token-segments (one per snippet/caption/best-segment) so the concept filter can check
    # PROXIMITY ("green" next to "shirt") within a single segment — not just co-presence, and
    # not across video shots. `flat_by_aid` is the union, for the single-keyword gate bypass.
    seg_by_aid: dict[str, list[list[str]]] = {}
    flat_by_aid: dict[str, set[str]] = {}
    if qtokens:
        for h in hits:
            parts = [h.snippet or "", h.caption or "", h.title or "", h.filename or ""]
            parts += evidence.get(h.asset_id, {}).get("rr_texts") or []
            segs = [decompose._seg_tokens(p) for p in parts if p]
            seg_by_aid[h.asset_id] = segs
            flat_by_aid[h.asset_id] = set().union(*segs) if segs else set()

    # P4 precision stage: cross-encoder rerank, blended with the fused score so
    # visually-matched (low-text) results aren't unfairly demoted. Skipped when no
    # hit has text (pure-image result sets) — the cross-encoder would just score
    # filenames and scramble the SigLIP visual ranking.
    # Only hits that actually carry text (documents, transcripts, captions) are
    # scored by the cross-encoder. Visual hits keep their SigLIP-fused score so the
    # reranker can never scramble the image ranking by scoring filenames.
    text_idx = [i for i, h in enumerate(hits) if h.snippet] if do_rerank else []
    # Set when the query has a CONFIDENT text answer (a result the reranker scored high, or a
    # literal keyword/OCR match). Drives the image-only noise suppression below.
    text_confident = False
    # Gate runs whenever there's a text hit — INCLUDING a lone candidate. A single weak
    # semantic match (a scene caption that reranks ~0 for an absent concept) must still face
    # the relevance gate; skipping it for len==1 let "pizza slice" / "spaceship" surface one
    # irrelevant caption each.
    if text_idx:
        # Rerank against the matched CONTENT only — never the title/filename. In this
        # system `title` defaults to the filename ("rashmika_hindi_audio.mp3"), and gluing
        # it on diluted a TRUE cross-lingual police match 0.743 → 0.051, dropping it under
        # the confidence gate and silently DISABLING the whole noise filter (13 junk hits).
        # Rerank the SEMANTIC content (the query-relevant segment), not whichever snippet
        # got stored first — BM25 keyword runs first and often stores a fuzzy/irrelevant
        # line for a multi-segment asset, which the reranker then (correctly) scores ~0,
        # wrongly dropping a genuinely relevant video/audio.
        # Rerank each text hit by its BEST candidate segment (max over the asset's top
        # snippets). A multi-segment video/audio is otherwise judged by one arbitrary line
        # — which the cross-encoder scores ~0, wrongly dropping a genuinely relevant asset.
        flat_idx: list[int] = []
        flat_pass: list[str] = []
        for i in text_idx:
            texts = evidence.get(hits[i].asset_id, {}).get("rr_texts") or [hits[i].snippet]
            for t in texts:
                if t:
                    flat_idx.append(i)
                    flat_pass.append(t)
        scores = await asyncio.to_thread(embed_client.rerank, req.q, flat_pass)
        if scores and len(scores) == len(flat_pass):
            fn = _minmax([h.score for h in hits])
            rr: dict[int, float] = {}
            for k, i in enumerate(flat_idx):
                if scores[k] > rr.get(i, -1.0):
                    rr[i] = scores[k]
            # ACCURACY-FIRST: the cross-encoder is a reliable relevance judge (scores true
            # matches high — police→पुलीस 0.743, beard captions high — and loose/noise ~0:
            # bald-head, cricket, "orphanage rent money"). So it is AUTHORITATIVE for text:
            # a text hit survives only if it clears rerank ≥ MIN. Only genuine VISUAL signals
            # (image/face, judged by their own thresholds upstream) bypass it.
            # EXCEPTION — literal full-term match: the cross-encoder is unreliable on bare
            # keyword queries ("man" → 0.011 even on a caption that literally says "the man
            # wears a blue polo shirt", because the caption leads with "two elderly adults").
            # So a hit whose text contains EVERY query content-word verbatim also survives.
            # This is safe — it does NOT re-admit the old short-query garbage, because that
            # noise never contained the literal terms (cricket/bald-head junk had neither word);
            # full literal coverage self-limits (long queries are rarely fully covered).
            def _literal(i: int) -> bool:
                # Plural-insensitive (mirrors the index's number-stemmer) so a literal keyword
                # match survives across grammatical number: "curtain" ⊆ a caption's "curtains".
                flat = flat_by_aid.get(hits[i].asset_id, set())
                return bool(qtokens) and {_deplural(w) for w in qtokens} <= {_deplural(w) for w in flat}

            def _name_hit(i: int) -> bool:
                # Query fully covered by the asset's TITLE/FILENAME tokens — a deliberate name
                # lookup ("MNDA", "ARUNI"), strong evidence even for a document, unlike an
                # incidental hit on one word buried in a long body.
                name = set(decompose._seg_tokens(hits[i].title or "")) \
                    | set(decompose._seg_tokens(hits[i].filename or ""))
                return bool(qtokens) and {_deplural(w) for w in qtokens} <= {_deplural(w) for w in name}
            # The cross-encoder is AUTHORITATIVE only when CONFIDENT — i.e. it found at least one
            # strong match for this query (some hit >= RERANK_CONFIDENT). Then a near-zero score
            # genuinely means "irrelevant" and we drop it. When the reranker is NOT confident
            # (paraphrase / cross-lingual / single-word queries it can't grade — it scores even
            # correct matches ~0), we CANNOT use it to reject, and fall back to the bi-encoder:
            # keep a hit whose dense cosine is strong (>= STRICT_DENSE_COS) or whose passage is in
            # a different SCRIPT than the query (cross-encoder is blind there). This is what splits
            # the legit low-rerank paraphrase ("reading about books" -> 0.561 dense) from the
            # same-script noise band ("pizza slice" -> 0.443, "spaceship" -> 0.455).
            confident = any(s >= C.RERANK_CONFIDENT for s in rr.values())

            def _keep(i: int) -> bool:
                sig = set(hits[i].matched_signals)
                if sig & {"image", "face"}:
                    return True
                # Caption/OCR ("seen") and speech ("said"/"transcript") grounding: an EXACT-or-
                # SYNONYM hit (fuzzy is off; brown↔beige, car↔vehicle) in SHORT visual/spoken text
                # is strong evidence, and the reranker is unreliable there (scores bare keywords
                # ~0). Always rescue — this is what keeps single-word synonym queries ("khaki",
                # "navy", "automobile") alive: BM25 knows khaki≈beige, the reranker doesn't.
                if sig & {"seen", "said", "transcript"}:
                    return True
                # Plain keyword / full literal coverage. For a caption- or transcript-bearing asset
                # this is strong grounding (terse text, exact-or-synonym hit). For a DOCUMENT it is
                # not enough on its own: the platform's own spec/BRD lists example queries ("temple",
                # "beach sunset", "running") that BM25 matches — and the cross-encoder even endorses,
                # since the word IS in that passage — though the document isn't ABOUT them. So a
                # document body hit is kept only when it's TOPICAL, proven by TWO independent
                # channels agreeing: a deliberate NAME lookup (query in title/filename — "MNDA",
                # "ARUNI"), OR independent SEMANTIC (dense) corroboration. The dense embedding fires
                # for a real topic ("requirements", "non-disclosure") but not for an example word
                # buried in a feature list, which cleanly separates topical from incidental. An
                # incidental doc keyword hit returns False here so the rerank gate below can't
                # re-admit it on the example word's literal presence.
                if sig & {"keyword"} or _literal(i):
                    if hits[i].type != "document":
                        return True
                    return bool(_name_hit(i) or sig & {"semantic"})
                # Rerank bypass. The cross-encoder is RELIABLE same-script (clears 0.10 = relevant),
                # but only SOMETIMES right across scripts — it nails police↔पुलीस (0.74) yet noise-
                # scores "pizza" vs a Hindi window at 0.11. So a cross-script passage must clear the
                # CONFIDENT bar (0.30), not the base 0.10, before its rerank score is trusted.
                cross = _cross_script(req.q, hits[i].snippet or "")
                if i in rr and rr[i] >= (C.RERANK_CONFIDENT if cross else C.RERANK_MIN_SCORE):
                    return True
                if confident:
                    return False   # trust the confident reranker's rejection
                # Reranker unreliable (paraphrase/cross-lingual). Rescue only STRONG SAME-SCRIPT
                # bi-encoder matches. Cross-script dense cosine is pure noise on this corpus — an
                # Arabic transcript scores 0.57 for "helicopter", higher than for its real topic
                # "orphanage" (0.45) — so a cross-script hit the reranker didn't endorse is dropped.
                if cross:
                    return False
                # The dense-cosine rescue is for PARAPHRASE (multi-word) queries the cross-encoder
                # mis-scores ("a person reading about books" → 0.56). A single concrete word with no
                # keyword/literal/image match and a ~0 rerank is simply ABSENT — don't rescue it on
                # cosine alone, or new captions drifting to ~0.55 for a random noun ("guitar",
                # "violin") leak back in as the corpus grows.
                if len(qtokens) < 2:
                    return False
                return evidence.get(hits[i].asset_id, {}).get("dense_cos", 0.0) >= C.STRICT_DENSE_COS

            kept = [i for i in range(len(hits)) if _keep(i)]
            # Did the query find a genuine TEXTUAL answer? (a high reranker score or a literal
            # keyword/OCR match). If so, ungrounded VISUAL hits are noise — drop them below.
            text_confident = any(rr.get(i, 0.0) >= C.RERANK_CONFIDENT for i in kept) \
                or any(_literal(i) for i in kept)

            # GROUNDING filter. When the query has a confident textual answer, a hit that survived
            # only on a weak SigLIP frame + floor-level semantic — with NO lexical/modality grounding
            # (keyword/OCR-seen/transcript), NO literal match, and NO reranker endorsement — is a
            # guess, not a match: "car" pulled two festival videos whose frames scored ~0.09 for
            # "car" though no shot contains one, while the real cars carry a "car" object label. Keep
            # a hit only if it's grounded; never empty the page.
            if text_confident:
                def _grounded(i: int) -> bool:
                    return (bool(set(hits[i].matched_signals) & {"keyword", "seen", "transcript",
                                                                 "said", "face"})
                            or _literal(i)
                            or (i in rr and rr[i] >= C.RERANK_MIN_SCORE))
                g = [i for i in kept if _grounded(i)]
                if g:
                    kept = g

            def _final(i: int) -> float:
                base = (0.8 * rr[i] + 0.2 * fn[i]) if i in rr else 0.5 * fn[i]
                # Soft main→sub cascade: ADD a boost for covering more of the query's concepts.
                if concepts:
                    base += C.COVERAGE_W * decompose.coverage(concepts, seg_by_aid.get(hits[i].asset_id, []))
                return base

            order = sorted(kept, key=_final, reverse=True)
            hits = [hits[i] for i in order]
            for h in hits:
                if h.snippet and "rerank" not in h.matched_signals:
                    h.matched_signals = h.matched_signals + ["rerank"]

    # Image-floor suppression for NAME/keyword answers that have no rerankable snippet. When the
    # only text hit is a filename/keyword match with no body highlight (a document found by its
    # NAME — "MNDA", "ARUNI"), the rerank+grounding stage above is skipped (text_idx is empty), so
    # bare image hits sitting at the SigLIP noise floor (sig == {"image"}, no lexical/semantic
    # corroboration, fused ~0.016) ride through as a flood of unrelated photos. If a grounded
    # textual answer exists, those images are guesses — drop them and keep the answer. Visual
    # queries are unaffected: their image hits carry caption snippets, so text_idx is non-empty and
    # this branch never runs; a gibberish query with no grounded answer is left alone (nothing to
    # anchor on — the documented tiny-corpus SigLIP floor, which resolves at scale).
    if do_rerank and not text_idx and req.q:
        grounded = any(set(h.matched_signals) & {"keyword", "seen", "said", "transcript", "face", "semantic"}
                       for h in hits)
        if grounded and any(set(h.matched_signals) == {"image"} for h in hits):
            hits = [h for h in hits if set(h.matched_signals) != {"image"}]

    # LLM relevance refine — OPT-IN (req.llm_refine), long queries only. In theory an LLM
    # judges the top candidates by meaning to drop tangential hits. MEASURED ON THIS BOX it
    # is impractical: the 8B VLM contends for VRAM with the resident models and reloads per
    # call → 17-46s/query, and judging caption TEXT wrongly drops visual matches ("a man
    # with a beard" → 0). Left off by default; a small always-warm text model + visual
    # judging for image hits would be needed to make it viable. Bounded + graceful when on.
    if getattr(req, "llm_refine", False) and do_rerank and lenient and len(hits) > 1:
        topk = hits[:12]
        items = [(h.snippet or h.caption or h.title or h.filename) for h in topk]
        rel = await asyncio.to_thread(embed_client.llm_filter, req.q, items)
        if rel is not None:
            relset = {i for i in rel if 0 <= i < len(topk)}
            hits = [topk[i] for i in range(len(topk)) if i in relset]
            for h in hits:
                if "ai-filter" not in h.matched_signals:
                    h.matched_signals = h.matched_signals + ["ai-filter"]

    # HARD concept filter — user intent "every word has to be there", with ATTRIBUTE BINDING.
    # For a multi-concept query a result must SATISFY every concept, else it's dropped. A bound
    # concept ("green shirt") is satisfied only when its words sit close together in one segment
    # — so "red shirt" no longer matches "red saree + gray shirt" or "blue shirt + red emblem".
    #   • Only require concepts the corpus CAN satisfy (`requirable`: every word seen somewhere).
    #     A word absent from all captions (cross-lingual "नीली", OCR-only) can't be a filter —
    #     it would drop everything — so the semantic/rerank path owns those (keeps native-script
    #     + paraphrase batteries working).
    #   • Fallback: if requiring them would empty the page, keep the unfiltered ranking — better
    #     to show the closest partial matches than a blank result ("don't kill the purpose").
    if concepts:
        required = [c for c in concepts if c["requirable"]]
        if required:
            # A VISUAL match (image/face) is exempt from the text-PRESENCE requirement — its
            # evidence is the pixels, and the VLM caption is often incomplete ("talking on phone"
            # → a photo of a man on a phone whose caption only says "man in uniform" must still
            # match). EXCEPT for COLOUR queries: SigLIP loosely matches a shirt regardless of
            # colour, so a white-shirt photo would leak into "red shirt" — colour binding needs
            # the strict text check. So the exemption applies only when no required concept names
            # a colour.
            color_query = any(w in decompose._COLORS for c in required for w in c["words"])

            def _visual(h) -> bool:
                return not color_query and bool(set(h.matched_signals) & {"image", "face"})
            full = [h for h in hits
                    if not (segs := seg_by_aid.get(h.asset_id))   # no text to judge → keep
                    or all(decompose.present(c, segs) for c in required)
                    or _visual(h)]
            if full:
                hits = full
            elif any(len(c["words"]) > 1 for c in required):
                # No exact bound match. Keep the closest (recall — "white shirt" may not be
                # literally captioned "white shirt"), but DROP results that bind the noun to the
                # WRONG colour — a green dupatta must not show for "red dupatta". Right-colour or
                # colour-unstated garments survive.
                hits = [h for h in hits
                        if not (segs := seg_by_aid.get(h.asset_id))
                        or not any(decompose.contradicts(c, segs) for c in required)]
            # else (plain multi-word, no binding): keep the unfiltered recall fallback.

    if req.sort == "type":
        hits.sort(key=lambda h: h.type)
    elif req.sort == "date":
        # newest first; assets without a date sort last. Tuple key avoids comparing a
        # datetime against a None/aware-vs-naive fallback (created_at share one tz origin).
        hits.sort(key=lambda h: (h.created_at is not None, h.created_at), reverse=True)

    # If the language filter or the rerank gate removed candidates, report the count the
    # user can actually see (avoids "4 results" displaying 2). On this corpus the pool
    # covers all candidates; at very large scale an exact post-gate total would need the
    # gate applied beyond the hydrated pool.
    if want_lang or len(hits) < len(pool_ids):
        total = len(hits)

    page = hits[req.offset: req.offset + req.limit]
    took = int((time.perf_counter() - t0) * 1000)
    await audit.log(db, user.id, "search", None, None, {"q": req.q, "total": total, "rerank": do_rerank})
    return SearchResponse(query=req.q, total=total, took_ms=took, hits=page,
                          concepts=concepts or None, intent=req.intent, degraded=degraded)


def _latin_ratio(s: str) -> float | None:
    """Fraction of a string's LETTERS that are ASCII/Latin. None if it has no letters."""
    letters = re.findall(r"[^\W\d_]", s or "", re.UNICODE)
    if not letters:
        return None
    return sum(1 for c in letters if c.isascii()) / len(letters)


def _cross_script(query: str, passage: str) -> bool:
    """True if query and passage are in clearly DIFFERENT scripts (one Latin-dominant, the
    other not). The cross-encoder is blind across scripts, so it can't be used to reject such a
    pair — we fall back to the bi-encoder's cosine floor instead. ('a man' vs an Arabic/Hindi
    transcript -> cross-script; English query vs English caption -> same.)"""
    q, p = _latin_ratio(query), _latin_ratio(passage)
    if q is None or p is None:
        return False
    return (q >= 0.6) != (p >= 0.6)


def _deplural(w: str) -> str:
    """Strip a single trailing plural 's' (curtains→curtain, shirts→shirt). Used to make the
    literal-keyword bypass number-insensitive, consistent with the index's plural stemmer."""
    return w[:-1] if len(w) > 3 and w.endswith("s") else w


def _minmax(xs: list[float]) -> list[float]:
    if not xs:
        return []
    lo, hi = min(xs), max(xs)
    if hi - lo < 1e-9:
        return [0.5] * len(xs)
    return [(x - lo) / (hi - lo) for x in xs]


@router.get("/suggest")
async def suggest(q: str, user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):
    """Search-as-you-type: things the library can ACTUALLY find for this prefix — object/
    action labels (from the structured tags) and named people. Grounded in the index, so
    every suggestion returns results. Best-effort: failures return []."""
    prefix = q.strip().lower()
    if len(prefix) < 2:
        return {"suggestions": []}
    out: list[dict] = []
    try:
        from ..models import Person
        rows = (await db.execute(select(Person.display_name).where(
            Person.display_name.ilike(f"{prefix}%")).limit(4))).scalars().all()
        out += [{"text": n, "type": "person"} for n in rows if n]
    except Exception:
        pass
    try:
        from ..search import opensearch_store
        agg = opensearch_store.client().search(index=C.OS_ASSETS, body={
            "size": 0,
            "aggs": {"lbl": {"terms": {"field": "labels", "size": 6,
                                       "include": f"{re.escape(prefix)}.*"}}}})
        out += [{"text": b["key"], "type": "label"}
                for b in agg["aggregations"]["lbl"]["buckets"]]
    except Exception:
        pass
    seen: set[str] = set()
    uniq = [s for s in out if not (s["text"].lower() in seen or seen.add(s["text"].lower()))]
    return {"suggestions": uniq[:8]}


@router.get("/facets")
async def facets(user: CurrentUser):
    """Distinct filterable values (department/project/language) with counts — feeds the
    filter bar so users can narrow without knowing the taxonomy. Best-effort."""
    try:
        from ..search import opensearch_store
        aggs = {k: {"terms": {"field": k, "size": 25}} for k in ("department", "project", "language")}
        res = opensearch_store.client().search(index=C.OS_ASSETS, body={"size": 0, "aggs": aggs})
        return {k: [{"value": b["key"], "count": b["doc_count"]}
                    for b in res["aggregations"][k]["buckets"]] for k in aggs}
    except Exception:
        return {"department": [], "project": [], "language": []}


@router.post("/face", response_model=SearchResponse)
async def search_by_face(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    file: UploadFile = File(...),
    limit: int = 24,
):
    """Facial search (BRD §5.7): upload a face image, find matching photos & video
    frames. Governed + audited (NFR-S4). Matches by ArcFace cosine over dam_face."""
    t0 = time.perf_counter()
    data = await file.read()
    key = f"tmp/face-query/{uuid.uuid4()}/{file.filename}"
    uri = put_object(key, data, file.content_type)

    faces = embed_client.detect_faces(uri, file.filename or "query.jpg")
    if not faces:
        await audit.log(db, user.id, "search_face", None, None, {"result": "no_face"})
        return SearchResponse(query="<face>", total=0,
                              took_ms=int((time.perf_counter() - t0) * 1000), hits=[])

    # Use the most confident face as the query. Threshold drops non-matches
    # (different people) so only genuine identity matches are returned.
    from ..search.constants import SCORE_THRESHOLDS
    face = max(faces, key=lambda f: f.get("det_score", 0))
    matches = qdrant_store.search(QDRANT_FACE, face["embedding"], limit=200,
                                  score_threshold=SCORE_THRESHOLDS.get(QDRANT_FACE))

    # Consent gating (NFR-S4): drop persons whose consent is denied/revoked.
    from .persons import denied_person_ids
    denied = await denied_person_ids(db)
    if denied:
        matches = [m for m in matches if m["payload"].get("person_id") not in denied]

    # Best match per asset, with the frame-mapped location of the hit.
    best: dict[str, dict] = {}
    for m in matches:
        aid = m["asset_id"]
        if aid not in best or m["score"] > best[aid]["score"]:
            best[aid] = m
    ordered = sorted(best.values(), key=lambda x: x["score"], reverse=True)[:limit]

    hits: list[SearchHit] = []
    if ordered:
        ids = [m["asset_id"] for m in ordered]
        rows = {a.id: a for a in (await db.execute(select(Asset).where(
            Asset.id.in_(ids), Asset.deleted_at.is_(None)))).scalars().all()}
        for m in ordered:
            a = rows.get(m["asset_id"])
            if not a:
                continue
            pl = m["payload"]
            timeline = []
            if pl.get("frame_index") is not None:
                timeline = [TimelineHit(frame_index=pl.get("frame_index"), smpte=pl.get("smpte"),
                                        kind="face", label=pl.get("person_id"), snippet=None)]
            hits.append(SearchHit(
                asset_id=a.id, type=a.type, title=a.title, filename=a.filename,
                thumbnail_uri=a.thumbnail_uri, score=round(m["score"], 4),
                matched_signals=["face"], snippet=None, timeline=timeline,
            ))

    await audit.log(db, user.id, "search_face", None, None,
                    {"matches": len(hits), "query_faces": len(faces)})
    return SearchResponse(query="<face>", total=len(best),
                          took_ms=int((time.perf_counter() - t0) * 1000), hits=hits)
