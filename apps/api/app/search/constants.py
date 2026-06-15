"""Index/collection names and vector dims — shared contract between API and ai-worker.

If you change a dim here, change it in services/ai-worker/worker/config.py too.
"""

# Qdrant collections (one per signal, per TSA §6.1)
QDRANT_TEXT = "dam_text"        # dense text + transcript embeddings (BGE-M3)
QDRANT_IMAGE = "dam_image"      # dense image/keyframe embeddings (OpenCLIP / Qwen3-VL-Embedding)
QDRANT_FACE = "dam_face"        # ArcFace face embeddings
QDRANT_DOCPAGE = "dam_docpage"  # ColQwen multi-vector page embeddings (late interaction)

# Vector dims
DIM_TEXT = 1024     # BGE-M3
DIM_IMAGE = 768     # OpenCLIP ViT-L-14 (and CLIP text encoder — shared space)
DIM_FACE = 512      # InsightFace ArcFace

# OpenSearch indices
OS_ASSETS = "dam-assets"            # asset-level keyword/metadata
OS_TRANSCRIPTS = "dam-transcripts"  # timed transcript segments (BRD §5.6 Smart Timeline)

# Minimum similarity for a vector hit to count as relevant. Vector search always
# returns the nearest top-k regardless of how far away they are, so without a floor
# the irrelevant "noise floor" leaks into results. Model-specific — tune per deploy.
#   SigLIP 2 text->image:      relevant band ~0.06-0.22, noise ~0.03-0.05 — and the
#                              absolute scale shifts with query specificity ("a woman"
#                              tops ~0.08 while "sqlite logo" tops ~0.22). A FIXED floor
#                              therefore kills recall on generic queries, so the image
#                              signal uses an ADAPTIVE cut (relative to the top hit) below.
#   BGE-M3 text:               real matches ~0.50-0.67; spurious (incl. cross-lingual) ~0.34-0.43
#   ArcFace face:              same person ~0.4+, different ~<0.1
SCORE_THRESHOLDS = {
    QDRANT_TEXT: 0.44,     # BGE-M3 noise floor. Spurious matches top out ~0.43; genuine
                           # cross-lingual hits on TERSE queries can sit just above (e.g. an
                           # Arabic shelter/orphanage clip scored 0.447 for "orphanage rent
                           # money"). 0.44 catches those without re-admitting the <=0.43 noise.
    QDRANT_IMAGE: 0.045,   # low FETCH floor; real cut is adaptive (IMAGE_REL_RATIO)
    QDRANT_FACE: 0.30,
}

# Image relevance is decided adaptively: keep hits scoring >= max(IMAGE_ABS_FLOOR,
# top_score * IMAGE_REL_RATIO). This scales with the query and drops the flat noise tail.
# Calibrated on real queries: present concepts' PRIMARY frame lands >=0.086 ("saree" 0.086,
# "couple embracing" 0.087, "beard" 0.11, "green shirt" 0.106), while most out-of-domain
# noise sits <=0.05 ("elephant"/"guitar"/"airplane"/"pizza" all ~0.048). Floor 0.08 cuts the
# noise tail (incl. "spaceship" 0.079) while keeping every present concept's top frame.
# HONEST LIMIT: on this tiny corpus SigLIP can't separate a rare absent word whose single
# closest frame coincidentally scores high ("skyscraper" 0.113 > "saree" 0.086) — raising the
# floor to kill it would also kill real garments. This residual resolves at scale as genuine
# matches climb to 0.15-0.25; the text channel (the real black-hole bug) is fixed separately.
IMAGE_REL_RATIO = 0.70
# 0.07, NOT higher: real garment frames sit as low as 0.079 ("red dress" secondary, a person
# in eyeglasses) — the SAME band as a rare absent word's single closest frame ("spaceship"
# 0.079). Raising the floor to kill the absent word also kills real garments, so we keep 0.07
# and accept that a handful of absent VISUAL concepts surface one weak frame on this tiny
# corpus (resolves at scale). The TEXT black-hole bug is fixed separately, where it's solvable.
IMAGE_ABS_FLOOR = 0.07

# Cross-encoder (reranker) relevance gate. The reranker reads the FULL query against
# each text-bearing passage and genuinely understands it: a near-zero score means
# "not relevant" (e.g. a 'saree' query vs a white-dress caption -> 0.077; 'hindi song'
# vs random dialogue -> <=0.025; real matches score 0.5-0.92). So we DROP text hits
# below this floor instead of merely reordering them. Visual (no-text) hits are not
# reranked and so are never gated here. Calibrated: noise <=0.08, real matches >=0.49.
# A dense (semantic) hit whose matched chunk is a SHORT fragment is untrustworthy: short
# transcript fillers embed near the centroid and clear the cosine floor for arbitrary queries
# (a search "black hole"). Such a hit is admitted only if it ALSO matches lexically (a query
# word appears in it) — otherwise it's dropped. Real cross-lingual/paraphrase matches ride on
# WINDOWED chunks (40+ words), so this never fires on them; it catches single-segment residue
# (a lone "🎵" or "sorry sir") and any not-yet-rewindowed asset.
MIN_DENSE_WORDS = 5

RERANK_MIN_SCORE = 0.12  # 0.10→0.12: the noise margin. Real same-script matches clear it
                         # comfortably (saree 0.17, beard/police 0.5-0.9); 0.10-0.12 was where
                         # the cross-encoder mis-scored stray single words ("pizza" vs a Hindi
                         # window at 0.11). Literal/keyword/image bypasses cover the rest.
# ...BUT the cross-encoder is weak on PARAPHRASE and CROSS-LINGUAL pairs — it scores
# them ~0.000 even when correct (English query vs Arabic/Tamil transcript). So the gate
# is only trustworthy when the reranker is CONFIDENT — i.e. it found at least one strong
# match (top score >= this). For low-confidence queries we keep the bi-encoder's semantic
# results rather than dropping them, preserving cross-lingual / paraphrase recall.
RERANK_CONFIDENT = 0.30
# ...but "keep the bi-encoder's results" must not re-admit the noise band. When the reranker is
# NOT confident, a text hit survives only if the BI-ENCODER is strongly about it — dense cosine
# at/above this — and ONLY for SAME-SCRIPT pairs. Cross-script cosine is noise on this corpus (an
# Arabic transcript scores 0.57 for "helicopter", above its real topic "orphanage" at 0.45), so
# it gets no rescue. Calibrated: genuine same-script paraphrase sits >=0.56 ("a person reading
# about books" -> a transcript window 0.561) while same-script noise on absent concepts tops out
# ~0.54 ("waterfall" -> military-shirt caption 0.539, "airplane" 0.526, "umbrella" 0.528). 0.55
# splits them. (The price: low-cosine cross-lingual paraphrase is lost — but as the numbers show,
# it was statistically indistinguishable from noise here anyway; it returns with corpus scale.)
STRICT_DENSE_COS = 0.55

# Query-decomposition cascade: additive boost (on the ~0..1 rerank-blend scale) for a result
# that covers ALL the query's concepts vs only one. 0.25 ≈ enough to flip near-ties toward the
# conjunctive match ("a man hanging") without overriding a clearly-stronger single-concept
# rerank. Multi-concept queries only; single-word queries are unaffected (concepts == []).
COVERAGE_W = 0.25
