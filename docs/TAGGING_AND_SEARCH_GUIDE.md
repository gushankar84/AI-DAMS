# Tagging, Preprocessing & Search — the Use-Case Correlation Guide

This is the "why does search work" reference: **every extraction step, every search layer,
and which user need each one exists to serve.** Updated June 2026 to match the running system.

---

## 1. PREPROCESSING — what is extracted at ingest, per asset type

### 1.1 Images
| step | model / method | produces | serves use case |
|---|---|---|---|
| Visual embedding | SigLIP 2 (ViT-B-16-512) | `dam_image` vector | "find by appearance" — *sunset couple, red car* |
| Face detect + embed | InsightFace ArcFace (512-d) | `dam_face` vectors + person link | people search, face-photo search |
| Person clustering | nearest-face ≥ 0.35 cosine; **in-job cache** so one person ≠ many clusters | `person` rows (name-once) | name a face once → find them everywhere |
| Object detection | YOLO11n (80 COCO classes) | object markers + labels | quick common objects (person, car, tie) |
| **Structured scene description** | Qwen3-VL, ONE pass, neutral prompt (never leading) | `PEOPLE` (garments + COLOURS) · `OBJECTS` (open-vocab: drum, hammock, lamp…) · `ACTIONS` · `INTENT` · `TEXT` (verbatim OCR, any script) | *"red shirt"*, *"drum"*, *"dancing"*, *"festival"*, signboard text |
| Caption → text index | BGE-M3 dense + OpenSearch body | semantic + keyword findability of everything the VLM saw | natural-language queries |
| Summary | caption doubles as description | "what is this image" in UI | quick understanding |

### 1.2 Videos (everything frame-mapped: frame index + SMPTE on the video's own grid)
| step | method | produces | serves use case |
|---|---|---|---|
| Frame map | ffprobe (fps_num/den, drop-frame) | exact frame↔time math | frame-accurate seek — the platform's core promise |
| Shot detection | PySceneDetect (cap 120/asset) | shot boundaries | the **unit of tagging** (per your spec: shot-wise, not frame-wise) |
| Per-shot keyframe → SigLIP | every shot | `dam_image` vectors w/ frame + smpte + frame_uri | visual search INSIDE videos; result shows the matching frame |
| Faces per shot | InsightFace + **InJobFaces cache** | one identity across a whole video | a recurring actor = ONE person, not 30 clusters |
| Objects per shot | YOLO11n | frame-mapped object markers | common-object timeline |
| **Structured tags per shot** | Qwen3-VL describe on a duration-capped sample (~1 per 11 s, min 6, max 30) | `objects[] / actions[] / intent` in each scene marker payload | *"where's the drum"* → exact shot; bounded cost (5-min video ≈ 28 captions ≈ ~28 min) |
| Speech (ASR) | Whisper large-v3 — GPU co-resident with the VLM, **VRAM guard** (falls to CPU if your own jobs hold the GPU) | time-aligned transcript → BM25 (`dam-transcripts`) + dense vectors | *"the clip where someone says X"* → seeks to that second |
| Asset summary | VLM text pass over shot tags + speech | one-sentence description, indexed | *"what is this video about"* |

### 1.3 Audio
Whisper ASR identical to video (frame-mapped to the audio stream grid). No music/instrument
tagging — **deliberately excluded per product decision** (speech = the audio content signal).

### 1.4 Documents
| step | method | produces | serves use case |
|---|---|---|---|
| Digital PDF | PyMuPDF **per page** with `[[PAGE n]]` sentinels | page-tagged text | **search a word/sentence → open AT that page** |
| Scanned PDF | VLM OCR per rendered page (multilingual, incl. Indic scripts) — also page-tagged | same | scanned/photographed docs equally page-jumpable |
| DOCX & others | Docling → markdown | clean body | tables/reading-order preserved |
| Chunking | per-page chunks → BGE-M3 vectors with `page` payload | `dam_text` chunks | semantic match knows its page |
| Summary | VLM over the text | description + indexed | "what is this document" |

### 1.5 Governance preprocessing (always on)
- sha256 **dedup** at upload (same bytes → same asset)
- **consent** status per person gates search; merge/split is reviewer-gated and consent is
  reconciled to the most restrictive
- reprocess is **idempotent** and preserves named identities (face vectors as re-link anchors)
- soft-delete (trash/restore); deleted assets excluded from search AND share links; audit log

---

## 2. SEARCH MECHANISMS — the layers a query passes through, in order

| # | layer | what it does | exists because |
|---|---|---|---|
| 1 | **Spell-correction** | SymSpell over the corpus's OWN vocabulary + curated attribute lexicon ("beerd"→"beard", "dhupata"→"dupatta"); UI shows *"showing results for …"* | users type fast; corrections must target words that actually exist here |
| 2 | **Query decomposition** | content words ranked by corpus rarity (IDF): MAIN = broad anchor ("man"), SUB = rare discriminator ("hanging"); N layers supported | *"both words must match"* — staged main→sub retrieval |
| 3 | **Attribute binding** | "red shirt" = ONE concept; colour must sit BEFORE a garment word within 3 tokens, same segment; garment synonyms (shirt⊇dress/kurta/top/dupatta…) | red *emblem* on a blue shirt ≠ red shirt; red *dress* counts |
| 4 | **Language narrowing** | "hindi song" drops assets detected as other languages | cross-language noise |
| 5 | **Hybrid retrieval (parallel)** | BM25 keyword + BGE-M3 dense text (captions/chunks/transcripts) + SigLIP text→image (images + video keyframes) + transcript BM25 → RRF fusion | each signal catches what the others miss |
| 6 | **Cross-encoder rerank** | bge-reranker-v2-m3 reads the query against each hit's BEST segments (max); authoritative gate ≥ 0.10 for text hits; visual/face hits bypass; **literal full-term match bypass** (reranker is unreliable on bare keywords); cross-lingual confidence guard | precision without killing recall |
| 7 | **Concept coverage** | soft boost for covering more concepts + **HARD filter**: every requirable concept must be present (bound concepts via proximity); no-match fallback prunes **contradictions** (a green dupatta never shows for "red dupatta") | "every word has to be there" |
| 8 | **Relevance dial** | user-set floor (default 0.44, the tuned value) | recall↔precision control in the UI |
| 9 | **Filters/facets** | type chips + department/project/language/date (facet counts from the index) | narrowing without knowing the taxonomy |
| 10 | **Result assembly** | matched-signal badges, em-highlighted snippet (XSS-safe), VLM caption fallback, **clickable timeline**: speech times / visual shots (`▶` seeks the player) / document pages (`p.N ▶` opens the PDF at that page), match-frame thumbnails | *transparency* (why did this match) + *go straight to the moment* |
| — | **Face search** | upload a face → ArcFace → nearest person → their consent-gated assets | "find this person" without a name |
| — | **Suggest-as-you-type** | prefix → object/action labels + person names FROM THE INDEX | every suggestion is guaranteed findable |

---

## 3. USE CASE → MECHANISM correlation (the quick map)

| you want to… | tagged by | found by | lands you at |
|---|---|---|---|
| find a spoken phrase | Whisper transcript (time-aligned) | transcript BM25 + dense | the exact second (▶ chip) |
| find "man in a red shirt" | VLM PEOPLE garments+colours | binding + hard filter + rerank | the image / the shot |
| find a drum / hammock / lamp | VLM OBJECTS per shot (open-vocab) | label + keyword + semantic | the exact shot (▶) |
| find people dancing / embracing | VLM ACTIONS per shot | label + semantic | the exact shot (▶) |
| find festival / procession scenes | VLM INTENT per shot | semantic + keyword | shot / asset |
| find a word in a document | per-page chunks (PDF text layer or page-OCR) | BM25 + dense chunks | **that page** (`p.N ▶` → `#page=N`) |
| find a signboard / on-image text | VLM TEXT (OCR, any script) | keyword + semantic | the image / shot |
| find all photos of Nani | face clustering + name-once | entities + person filter | their assets |
| find this face (photo in hand) | ArcFace vectors | `/search/face` | consent-gated matches |
| search with typos | corpus SymSpell | layer 1 + banner | corrected results, transparently |
| search in Hindi/Tamil script | BGE-M3 multilingual + multilingual OCR | dense + confidence-guarded rerank | native-script matches |
| find by look ("sunset beach couple") | SigLIP keyframe/image vectors | text→image | image / matching frame |
| "what is this video/doc about" | VLM summary | shown as description + searchable | asset page |
| narrow by team/date | upload metadata | facets + filters | filtered results |
| share safely outside | watermark + expiry + consent gates | distribution links | public page, revocable |

---

## 4. Honest limits (as of June 2026)
- **Deep paraphrase across languages** ("orphanage rent money" → an Arabic transcript) can
  still miss — the reranker scores deep cross-lingual paraphrase ~0; semantic recall partially
  covers it. Known, documented, the one battery WARN.
- **No music/instrument audio tagging** — speech only, by product decision.
- Page-jump applies to **PDFs** (browsers honor `#page=`); DOCX opens whole.
- VLM tags only exist on assets (re)processed since the structured pipeline landed.
- No EXIF capture-date/GPS, no near-duplicate (perceptual hash) detection yet.
