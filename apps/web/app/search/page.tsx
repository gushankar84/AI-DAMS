"use client";
import { Suspense, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { search, faceSearch, searchSuggest, searchFacets, SearchHit, QueryConcept, Suggestion, FacetValue } from "@/lib/api";
import AppShell from "@/components/AppShell";
import AssetCard from "@/components/AssetCard";

type TypeFilter = "" | "document" | "image" | "video" | "audio";
type SortMode = "relevance" | "date" | "type";

const SORT_CHIPS: { key: SortMode; label: string }[] = [
  { key: "relevance", label: "Best match" },
  { key: "date", label: "Newest" },
  { key: "type", label: "Type" },
];

const TYPE_CHIPS: { key: TypeFilter; label: string }[] = [
  { key: "", label: "All" },
  { key: "document", label: "Documents" },
  { key: "image", label: "Images" },
  { key: "video", label: "Videos" },
  { key: "audio", label: "Audio" },
];

function SearchInner() {
  const params = useSearchParams();
  const router = useRouter();
  const initialQ = params.get("q") || "";

  const [q, setQ] = useState(initialQ);
  const [type, setType] = useState<TypeFilter>("");
  const [rerank, setRerank] = useState(true);
  const [minScore, setMinScore] = useState(0.44);   // relevance floor (tuned default); dial below
  const [sort, setSort] = useState<SortMode>("relevance");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [total, setTotal] = useState(0);
  const [took, setTook] = useState(0);
  const [concepts, setConcepts] = useState<QueryConcept[]>([]);  // main→sub decomposition
  const [corrected, setCorrected] = useState<string | null>(null); // spell-corrected query (transparency)
  const [degraded, setDegraded] = useState(false); // model server down → keyword-only this query
  // Search-as-you-type: grounded suggestions (labels the index can find + named people).
  const [sugg, setSugg] = useState<Suggestion[]>([]);
  const [suggOpen, setSuggOpen] = useState(false);
  // Filter bar: narrow by department/project/language/date (params the API always supported).
  const EMPTY_FLT = { department: "", project: "", language: "", date_from: "", date_to: "" };
  const [flt, setFlt] = useState(EMPTY_FLT);
  const [fltOpen, setFltOpen] = useState(false);
  const [facets, setFacets] = useState<Record<string, FacetValue[]> | null>(null);
  // Modality intent: lean toward what was SAID / SEEN / WRITTEN ("" = anywhere). Soft —
  // reorders, never hides. The backend may also auto-detect it from phrasing.
  const [intent, setIntent] = useState<"" | "spoken" | "visual" | "written">("");
  const [appliedIntent, setAppliedIntent] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);
  const [label, setLabel] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  async function runSearch(query: string, t: TypeFilter, rr: boolean, ms: number = minScore,
                           srt: SortMode = sort, append = false, fltOverride?: typeof EMPTY_FLT,
                           intentOverride?: "" | "spoken" | "visual" | "written") {
    const text = query.trim();
    if (!text) return;
    const fv = fltOverride ?? flt;
    const iv = intentOverride ?? intent;
    // Keep the query in the URL so the browser Back button (from an asset page) returns
    // to this exact search instead of an empty box.
    if (params.get("q") !== text) router.replace(`/search?q=${encodeURIComponent(text)}`, { scroll: false });
    setLoading(true);
    setSearched(true);
    setLabel(text);
    try {
      const off = append ? hits.length : 0;   // append => fetch the NEXT page
      const res = await search({ q: text, types: t ? [t] : null, limit: 36, offset: off,
                                 rerank: rr, min_score: ms, sort: srt,
                                 intent: iv || null,
                                 department: fv.department || null, project: fv.project || null,
                                 language: fv.language || null,
                                 date_from: fv.date_from || null, date_to: fv.date_to || null });
      setHits(append ? [...hits, ...res.hits] : res.hits);
      setTotal(res.total);
      setTook(res.took_ms);
      setConcepts(res.concepts || []);
      setAppliedIntent(res.intent || null);
      // Transparency: the backend silently fixes typos ("beerd"→"beard"); if it searched a
      // different string than the user typed, SAY so — never let results mismatch the box.
      setCorrected(res.query && res.query.trim().toLowerCase() !== text.toLowerCase() ? res.query : null);
      setDegraded(!!res.degraded);
    } catch (err: unknown) {
      if (err instanceof Error && err.message === "unauthorized") return;
      setHits([]);
      setTotal(0);
      setTook(0);
    } finally {
      setLoading(false);
    }
  }

  async function runFaceSearch(file: File) {
    setLoading(true);
    setSearched(true);
    setLabel("Face search");
    try {
      const res = await faceSearch(file);
      setHits(res.hits);
      setTotal(res.total);
      setTook(res.took_ms);
      setConcepts(res.concepts || []);
      setCorrected(null);
    } catch (err: unknown) {
      if (err instanceof Error && err.message === "unauthorized") return;
      setHits([]);
      setTotal(0);
      setTook(0);
    } finally {
      setLoading(false);
    }
  }

  // Run the URL's query on mount AND whenever it changes — e.g. the browser Back button
  // from an asset page returns to /search?q=… ; re-sync the box + re-run instead of
  // showing a stale prior search. Guarded by `label` so a search we just issued (which
  // updates the URL) doesn't loop.
  useEffect(() => {
    if (initialQ.trim() && initialQ !== label) {
      setQ(initialQ);
      runSearch(initialQ, type, rerank);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialQ]);

  // Search-as-you-type: debounced suggestions for the current prefix (grounded in the
  // index — every suggestion is something the library can actually find).
  useEffect(() => {
    const text = q.trim();
    if (text.length < 2) { setSugg([]); return; }
    const t = setTimeout(() => {
      searchSuggest(text).then((r) => setSugg(r.suggestions || [])).catch(() => setSugg([]));
    }, 250);
    return () => clearTimeout(t);
  }, [q]);

  function changeFilter(patch: Partial<typeof EMPTY_FLT>) {
    const next = { ...flt, ...patch };
    setFlt(next);
    if (searched && q.trim()) runSearch(q, type, rerank, minScore, sort, false, next);
  }
  const activeFlt = Object.values(flt).filter(Boolean).length;

  // Re-run when filters change, but only if a text search has already happened.
  function changeType(next: TypeFilter) {
    setType(next);
    if (searched && q.trim()) runSearch(q, next, rerank);
  }
  function toggleRerank() {
    const next = !rerank;
    setRerank(next);
    if (searched && q.trim()) runSearch(q, type, next);
  }
  function changeSort(next: SortMode) {
    setSort(next);
    if (searched && q.trim()) runSearch(q, type, rerank, minScore, next);
  }
  function loadMore() {
    if (!loading && q.trim()) runSearch(q, type, rerank, minScore, sort, true);
  }

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    runSearch(q, type, rerank);
  }

  function onPickFace(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) runFaceSearch(file);
    e.target.value = "";
  }

  return (
    <AppShell title="Search" subtitle="Find anything — spoken words, people, objects, documents">
      <form className="searchbar" onSubmit={onSubmit} style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <span style={{ position: "relative", flex: 1, minWidth: 240 }}>
          <input
            className="field"
            style={{ width: "100%" }}
            value={q}
            onChange={(e) => { setQ(e.target.value); setSuggOpen(true); }}
            onFocus={() => setSuggOpen(true)}
            onBlur={() => setTimeout(() => setSuggOpen(false), 150)}
            onKeyDown={(e) => { if (e.key === "Escape") setSuggOpen(false); }}
            placeholder="Search everything — a spoken phrase, a person, an object, a document…"
            autoFocus
          />
          {suggOpen && sugg.length > 0 && (
            <div style={{ position: "absolute", top: "calc(100% + 4px)", left: 0, right: 0, zIndex: 40,
                          background: "var(--panel, #15171c)", border: "1px solid var(--border)",
                          borderRadius: 10, overflow: "hidden", boxShadow: "0 8px 24px rgba(0,0,0,.4)" }}>
              {sugg.map((s) => (
                <div key={`${s.type}:${s.text}`}
                  // onMouseDown fires before the input's blur, so the click always lands.
                  onMouseDown={() => { setQ(s.text); setSuggOpen(false); runSearch(s.text, type, rerank); }}
                  style={{ display: "flex", justifyContent: "space-between", alignItems: "center",
                           padding: "8px 12px", cursor: "pointer", fontSize: 13 }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = "rgba(120,140,255,0.08)")}
                  onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}>
                  <span>{s.text}</span>
                  <span className="badge" style={{ opacity: 0.7 }}>{s.type === "person" ? "👤 person" : "tag"}</span>
                </div>
              ))}
            </div>
          )}
        </span>
        <button type="submit" className="btn">Search</button>
        <button type="button" className="btn ghost" onClick={() => fileRef.current?.click()}
                title="Upload a photo of a face — finds every photo/video in the library where that person appears">
          Search by face
        </button>
        <input ref={fileRef} type="file" accept="image/*" onChange={onPickFace} style={{ display: "none" }} />
      </form>

      <div className="filters" style={{ marginTop: 12 }}>
        {TYPE_CHIPS.map((c) => (
          <span
            key={c.key || "all"}
            className={"chip" + (type === c.key ? " active" : "")}
            onClick={() => changeType(c.key)}
          >
            {c.label}
          </span>
        ))}
        <span className={"chip" + (rerank ? " active" : "")} onClick={toggleRerank}
              title="AI double-checks each result against your words and re-orders by true relevance. Leave on; turn off only for a raw, faster search.">
          Rerank: {rerank ? "on" : "off"}
        </span>
        <span
          className="chip"
          style={{ display: "inline-flex", alignItems: "center", gap: 8, cursor: "default" }}
          title="Match strictness — slide LEFT to see more (looser) results, RIGHT for only the best matches. 0.44 is the tuned default."
        >
          Relevance
          <input
            type="range" min={0.30} max={0.60} step={0.02} value={minScore}
            title="Slide left = more results · right = only best matches"
            onChange={(e) => {
              const v = parseFloat(e.target.value);
              setMinScore(v);
              if (searched && q.trim()) runSearch(q, type, rerank, v);
            }}
            style={{ width: 92, accentColor: "var(--accent)" }}
          />
          <span style={{ fontVariantNumeric: "tabular-nums", minWidth: 28 }}>{minScore.toFixed(2)}</span>
        </span>
        {/* Modality intent: where should the words count — speech, visuals, or documents? */}
        {([["", "Anywhere"], ["spoken", "Spoken"], ["visual", "Visible"], ["written", "Written"]] as const).map(([k, lbl]) => (
          <span key={k || "any"}
                className={"chip" + (intent === k ? " active" : "")}
                title={k === "spoken" ? "Words someone SAYS (transcripts)"
                     : k === "visual" ? "Things you can SEE (frames, objects, clothing)"
                     : k === "written" ? "Words WRITTEN in documents and on screen"
                     : "Search everywhere"}
                onClick={() => {
                  setIntent(k);
                  if (searched && q.trim()) runSearch(q, type, rerank, minScore, sort, false, undefined, k);
                }}>
            {lbl}
          </span>
        ))}
        <span style={{ width: 1, alignSelf: "stretch", background: "var(--border)", margin: "0 4px" }} />
        <span className={"chip" + (fltOpen || activeFlt ? " active" : "")}
              title="Narrow by department, project, language or date"
              onClick={async () => {
                const open = !fltOpen;
                setFltOpen(open);
                if (open && !facets) { try { setFacets(await searchFacets()); } catch { setFacets({}); } }
              }}>
          Filters{activeFlt ? ` · ${activeFlt}` : ""}
        </span>
        <span style={{ width: 1, alignSelf: "stretch", background: "var(--border)", margin: "0 4px" }} />
        <span className="muted" style={{ fontSize: 12, alignSelf: "center" }}>Sort</span>
        {SORT_CHIPS.map((c) => (
          <span
            key={c.key}
            className={"chip" + (sort === c.key ? " active" : "")}
            onClick={() => changeSort(c.key)}
          >
            {c.label}
          </span>
        ))}
      </div>

      {fltOpen && (
        <div className="filters" style={{ marginTop: 8, alignItems: "center", gap: 8 }}>
          {(["department", "project", "language"] as const).map((k) => (
            <select key={k} className="field" style={{ width: "auto", fontSize: 13, padding: "5px 8px" }}
                    value={flt[k]} onChange={(e) => changeFilter({ [k]: e.target.value })}>
              <option value="">{k[0].toUpperCase() + k.slice(1)}: any</option>
              {(facets?.[k] || []).map((f) => (
                <option key={f.value} value={f.value}>{f.value} ({f.count})</option>
              ))}
            </select>
          ))}
          <span className="muted" style={{ fontSize: 12 }}>from</span>
          <input type="date" className="field" style={{ width: "auto", fontSize: 13, padding: "5px 8px" }}
                 value={flt.date_from} onChange={(e) => changeFilter({ date_from: e.target.value })} />
          <span className="muted" style={{ fontSize: 12 }}>to</span>
          <input type="date" className="field" style={{ width: "auto", fontSize: 13, padding: "5px 8px" }}
                 value={flt.date_to} onChange={(e) => changeFilter({ date_to: e.target.value })} />
          {activeFlt > 0 && (
            <span className="chip" onClick={() => changeFilter(EMPTY_FLT)}>Clear filters</span>
          )}
        </div>
      )}

      {searched && (
        <div className="muted" style={{ marginTop: 12, display: "flex", alignItems: "center", gap: 8 }}>
          {loading ? (
            <>
              <span className="spinner" /> Searching…
            </>
          ) : (
            <span>
              {label ? `“${label}” — ` : ""}{total} results · {took} ms
            </span>
          )}
          {!loading && corrected && (
            // Never search a different string than the user typed without SAYING so.
            <span style={{ marginLeft: 10, fontSize: 12, color: "var(--amber, #f5a623)" }}>
              showing results for “<b>{corrected}</b>” (typo-corrected)
            </span>
          )}
          {!loading && appliedIntent && !intent && (
            // The backend inferred intent from phrasing ("talks about …") — say so.
            <span style={{ marginLeft: 10, fontSize: 12, color: "var(--muted)" }}>
              leaning toward <b>{appliedIntent === "spoken" ? "spoken words" : appliedIntent === "visual" ? "what's visible" : "written text"}</b>
            </span>
          )}
          {!loading && degraded && (
            // The model server was unreachable, so semantic + visual search dropped out and only
            // keyword matching ran. SAY so — otherwise shrunken results read as "nothing matches".
            <div style={{ marginTop: 8, fontSize: 12, color: "var(--amber, #f5a623)",
                          border: "1px solid var(--amber, #f5a623)", borderRadius: 6, padding: "6px 10px",
                          background: "rgba(245,166,35,0.08)" }}>
              ⚠ Semantic &amp; visual search temporarily unavailable — showing <b>keyword matches only</b>.
              Some results may be missing; try again shortly.
            </div>
          )}
          {!loading && concepts.length >= 2 && (
            // Show how the query was read: main (broad) → sub (rare). Results that cover MORE
            // of these rank higher — the "a man hanging" cascade made visible.
            <span style={{ display: "inline-flex", alignItems: "center", gap: 6, marginLeft: 12,
              fontSize: 12, color: "var(--muted)", flexWrap: "wrap" }}>
              <span style={{ opacity: 0.7 }}>read as</span>
              {concepts.map((c, i) => (
                <span key={c.term} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                  {i > 0 && <span style={{ opacity: 0.5 }}>→</span>}
                  <span title={`${c.role} · in ${c.df} assets`} style={{
                    padding: "1px 8px", borderRadius: 999, border: "1px solid var(--border)",
                    background: i === 0 ? "var(--accent-soft, rgba(120,140,255,0.12))" : "transparent",
                    color: i === 0 ? "var(--accent)" : "var(--muted)" }}>
                    {c.term}{i === 0 ? " · main" : ""}
                  </span>
                </span>
              ))}
            </span>
          )}
        </div>
      )}

      {!loading && searched && hits.length === 0 && (
        <div className="empty">No matches. Try a different phrase, a name, or an object you remember seeing.</div>
      )}

      {hits.length > 0 && (
        <div className="grid" style={{ marginTop: 16 }}>
          {hits.map((h) => (
            <AssetCard
              key={h.asset_id}
              id={h.asset_id}
              type={h.type}
              title={h.title}
              filename={h.filename}
              thumbnailUri={h.thumbnail_uri}
              snippet={h.snippet}
              caption={h.caption}
              signals={h.matched_signals}
              timeline={h.timeline}
            />
          ))}
        </div>
      )}

      {hits.length > 0 && hits.length < total && (
        <div style={{ display: "flex", justifyContent: "center", marginTop: 20 }}>
          <button className="btn ghost" onClick={loadMore} disabled={loading}>
            {loading ? "Loading…" : `Load more (${hits.length} of ${total})`}
          </button>
        </div>
      )}
    </AppShell>
  );
}

export default function SearchPage() {
  return (
    <Suspense fallback={<AppShell title="Search"><div className="empty"><span className="spinner" /></div></AppShell>}>
      <SearchInner />
    </Suspense>
  );
}
