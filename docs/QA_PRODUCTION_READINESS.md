# Production Readiness Report — AI DAM Platform

**Date:** 2026-06-10  ·  **Scope:** Full-system QA prior to client delivery
**Verdict:** ✅ **Production-ready** for the target deployment (internal media team, single-GPU host), with documented residual risks and a horizontal-scaling path.

---

## 1. Test coverage & results

| Area | Suite | Result |
|---|---|---|
| Search correctness | `qa_search.py` (23 queries: garment+colour, scene, OCR, objects, people, docs, cross-lingual, negatives, edge/injection/Devanagari) | **22 PASS / 1 WARN / 0 FAIL** |
| Performance (single) | `qa_latency.py`, `qa_backends.py` | p50 **143 ms**, max 657 ms |
| API / auth / validation | `qa_api.py` (17 checks) | **17 / 0** |
| Ingestion / idempotency | `qa_ingest.py` (11 checks) | **11 / 0** |
| Reliability / degradation | model-server kill + restart | **graceful keyword fallback, full recovery** |
| Governance / security | `qa_governance.py` (8 checks) | **8 / 0** |
| Concurrency / scalability | `qa_concurrency.py` (N=1..48) | **100% success; GPU-bound ceiling ~2.8 req/s** |
| UI journey | preview: login → search → asset viewer, all 10 routes | **renders, caption snippets, 0 console errors** |

---

## 2. Bugs found & fixed during QA

1. **🔴 Search latency 5.4 s → 0.25 s (>20×).** The API's `embed_client` called the model server at `localhost:8100` with a new connection per call. On Windows `localhost` resolves to IPv6 `::1` first; the IPv4-only server forced a ~2.4 s fallback **per embed call**. Fix: persistent pooled `httpx.Client` + force `127.0.0.1`. (`app/search/embed_client.py`)

2. **🔴 Orphan vectors / non-idempotent re-ingest.** `clear_derived`'s Qdrant delete ran `wait=False`, racing the pipeline's re-insert and leaving duplicate vectors (15 images affected). Fix: `wait=True` on delete + upsert; orphans purged. (`worker/stores.py`)

3. **🟠 Malformed UUID path → HTTP 500.** A non-UUID asset id failed at asyncpg parameter-encoding (generic `DBAPIError`). Fix: global handler returns **422** for UUID/data errors, **500** otherwise. (`app/main.py`)

4. **🟠 Concurrency serialization.** The blocking `hybrid_search()` ran on the async event loop, serializing concurrent requests. Fix: `asyncio.to_thread()` for `hybrid_search` + `rerank`. (`app/routers/search.py`)

**Precision tuning (2):** image noise floor `0.052 → 0.07` (cuts out-of-domain noise <0.02 while keeping signal >0.077); BM25 `minimum_should_match: 75%` (kills single-fuzzy-term leaks like "Saturn"). Negative queries went 5→0 ("spaceship"), 7→1 ("quantum"). (`app/search/constants.py`, `opensearch_store.py`)

---

## 3. Performance

- **Single-user search latency:** p50 143 ms, p95 ~250 ms (sub-second target met).
- **Backends:** OpenSearch 50 ms, Qdrant 8–50 ms, BGE-M3 37 ms, SigLIP 9 ms, reranker 47 ms.
- **Concurrency:** 100% success to N=48; throughput ~2.8 req/s, GPU-bound on the single model server.

---

## 4. Known residual risks & mitigations

| Risk | Severity | Mitigation / path |
|---|---|---|
| **Gibberish queries** can surface ~few images on a tiny corpus (SigLIP gives random text ~0.08, inside the signal band) | Low | Documented; resolves at scale as real matches climb to 0.15–0.25. Not worth hurting generic-query recall to chase nonsense input. |
| **Throughput ceiling ~2.8 req/s** on one GPU | Med (scale only) | Horizontal scaling: model-server replicas behind a balancer, or batching inference server (TEI/vLLM, per `docs/SCALE_AND_DR.md`). Far above expected internal load. |
| **Pagefile 10 GB → commit ~43–48 GB, often near-full** | Med (ops) | Raise pagefile to 48–64 GB (host setting). Run one heavy model at a time (resident/evict design); unload Ollama between batches. |
| **Silent song clips** (the supplied UHD corpus has no audio track) | N/A (data) | Lyrics search needs the actual song audio files; clips are visual-search-only (captions/faces/objects). |
| VLM ingest cost at scale (Qwen3-VL 8B ~8 s/image) | Med (scale) | Run captioning as async/batch enrichment; drop to Qwen3-VL 4B for bulk; sample video shots. |

---

## 5. Best practices verified

- **Auth:** all mutating/read endpoints reject anonymous (401); bad tokens/passwords rejected; JWT.
- **Privacy:** sensitive files (Aadhaar/cheque/policy) confirmed excluded from the index; no leaks on sensitive search terms.
- **Governance:** searches audited; consent state on persons; face search auth-gated + consent-gated.
- **Resilience:** graceful degradation to keyword when the embedding service is down; full recovery on restart; data persists across host reboot (Docker volumes).
- **Idempotency:** re-ingestion is clean (no orphan vectors), verified end-to-end.
- **Input safety:** injection-like strings treated as literal text (parameterized queries); malformed input → 4xx not 5xx.

---

## 6. Scenario coverage (proactive user-behavior simulation)

Beyond fixed test cases, we simulated the realistic space of user behavior and validated correctness:

| Dimension | Scenarios | Result |
|---|---|---|
| Search intents | modality, attribute/colour, object, face, on-image text, cross-lingual, named person, language-qualified | pass |
| **Input variations** | misspellings, question form, native-script (Hindi/Tamil/Telugu/Arabic), compound multi-attribute, synonyms/paraphrase, casing/whitespace | **18/18** |
| **Filters / sort / pagination / boundaries** | type/language/date filters, sort relevance/type/date, offset/limit (overlap, beyond-end), limit 0/huge, negative offset, whitespace | **14/14** |
| **Workflows** | face search + consent-gating (deny → excluded), collections CRUD, share + expiry + watermark, asset detail transcript + frame-mapped timeline, soft-delete + restore | **10/10** |
| Error handling | malformed IDs across collections/persons/shares/workflow/media → 422 | 5/5 |

**Bugs found & fixed in scenario testing:**
- **`sort=date` was unimplemented** (router only handled `sort=type`) → implemented date sort + added `created_at` to `SearchHit`.
- **Date-range filter ignored by vector signals** (only BM25 honored `date_from/date_to`) → applied post-hoc on hydrated assets so all signals respect it.
- **Relevance precision/recall** (prior round): confidence-gated reranker + language-aware narrowing + visual-match exception (saree/hindi-song fixed, cross-lingual preserved).

**Descoped:** role-based access control testing (per user request).

**Residual WARNs (documented, minor):** gibberish strings surface a few language-less images (SigLIP noise floor); an object that exists only as a label on a caption-less silent clip ("umbrella") can be out-ranked by caption-rich clips. Both resolve at scale / with re-captioning.

---

*Artifacts:* `scripts/qa_*.py` + `scripts/sc_*.py` (re-runnable suites), `.data/*_report.txt` (reports).
