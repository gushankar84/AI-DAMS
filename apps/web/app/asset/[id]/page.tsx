"use client";
import { useEffect, useRef, useState } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import {
  getAsset, mediaUrl, assetText, updateAsset, deleteAsset, reprocessAsset,
  transitionWorkflow, workflowHistory, listCollections, addToCollection, createShare,
  fmtBytes, fmtClock, TYPE_ICON,
  type AssetDetail, type Marker, type Transcript, type Collection, type Share, type DocText,
} from "@/lib/api";
import AppShell from "@/components/AppShell";
import DocReader from "@/components/DocReader";

type Tab = "transcript" | "detections" | "metadata" | "workflow";
type Hist = { state: string; actor_id: string | null; note: string | null; created_at: string };

const WF_BADGE: Record<string, string> = {
  approved: "green", published: "green", under_review: "amber",
  draft: "amber", archived: "red", rejected: "red",
};
const WF_ACTIONS: { state: string; label: string }[] = [
  { state: "under_review", label: "Submit for review" },
  { state: "approved", label: "Approve" },
  { state: "published", label: "Publish" },
  { state: "archived", label: "Archive" },
];

export default function AssetViewer() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const qs = useSearchParams();
  const mediaRef = useRef<HTMLVideoElement & HTMLAudioElement>(null);

  const [asset, setAsset] = useState<AssetDetail | null>(null);
  const [url, setUrl] = useState<string | null>(null);
  const [docText, setDocText] = useState<DocText | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<Tab>("transcript");
  const [toast, setToast] = useState<string | null>(null);

  // Metadata form
  const [form, setForm] = useState({
    title: "", description: "", tags: "", department: "", project: "", rights: "", language: "",
  });
  const [saving, setSaving] = useState(false);

  // Workflow
  const [history, setHistory] = useState<Hist[]>([]);
  const [busy, setBusy] = useState(false);

  // Modals
  const [collOpen, setCollOpen] = useState(false);
  const [collections, setCollections] = useState<Collection[] | null>(null);
  const [shareOpen, setShareOpen] = useState(false);
  const [sharePerm, setSharePerm] = useState("view");
  const [shareWm, setShareWm] = useState(false);
  const [share, setShare] = useState<Share | null>(null);
  const [shareBusy, setShareBusy] = useState(false);

  function flash(msg: string) {
    setToast(msg);
    setTimeout(() => setToast(null), 2600);
  }

  async function load() {
    try {
      const a = await getAsset(id);
      setAsset(a);
      setForm({
        title: a.title ?? "", description: a.description ?? "", tags: (a.tags ?? []).join(", "),
        department: a.department ?? "", project: a.project ?? "", rights: a.rights ?? "", language: a.language ?? "",
      });
      const hasTranscript = a.transcript?.length > 0;
      const hasMarkers = a.markers?.length > 0;
      setTab(hasTranscript ? "transcript" : hasMarkers ? "detections" : "metadata");
    } catch (err: any) {
      if (err?.message === "unauthorized") return;
      router.push("/explorer");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    let alive = true;
    setLoading(true);
    load();
    mediaUrl(id, "original").then((u) => { if (alive) setUrl(u); });
    assetText(id).then((t) => { if (alive) setDocText(t); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  function seek(seconds: number | null) {
    if (seconds == null || !mediaRef.current) return;
    mediaRef.current.currentTime = seconds;
    mediaRef.current.play().catch(() => {});
  }

  // Deep-link to the matched MOMENT: /asset/{id}?f=<frame_index> (set by a search result's
  // timeline chip) seeks the player there once media + markers are loaded — so "search finds
  // the moment" holds end-to-end instead of dumping the user at 0:00.
  useEffect(() => {
    const f = qs.get("f");
    if (!f || !asset || !url) return;
    const target = parseInt(f, 10);
    if (Number.isNaN(target)) return;
    const cands = (asset.markers || []).filter((m) => m.frame_index != null && m.start_seconds != null);
    if (!cands.length) return;
    const nearest = cands.reduce((a, b) =>
      Math.abs((b.frame_index as number) - target) < Math.abs((a.frame_index as number) - target) ? b : a);
    const secs = nearest.start_seconds as number;
    const el = mediaRef.current;
    if (!el) return;
    const apply = () => { el.currentTime = secs; };
    if (el.readyState >= 1) apply();
    else el.addEventListener("loadedmetadata", apply, { once: true });
  }, [asset, url, qs]);

  async function save() {
    setSaving(true);
    try {
      await updateAsset(id, {
        title: form.title, description: form.description,
        tags: form.tags.split(",").map((t) => t.trim()).filter(Boolean),
        department: form.department, project: form.project, rights: form.rights, language: form.language,
      });
      await load();
      flash("Metadata saved");
    } catch (err: any) {
      if (err?.message !== "unauthorized") flash(`Save failed${err?.message ? `: ${err.message}` : ""} — your changes were NOT stored`);
    } finally {
      setSaving(false);
    }
  }

  async function loadHistory() {
    try { setHistory(await workflowHistory(id)); } catch { /* non-fatal */ }
  }

  async function transition(state: string) {
    setBusy(true);
    try {
      await transitionWorkflow(id, state);
      await load();
      await loadHistory();
      flash(`Moved to ${state.replace(/_/g, " ")}`);
    } catch (err: any) {
      if (err?.message !== "unauthorized") flash("Transition failed");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (tab === "workflow") loadHistory();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  async function openCollections() {
    setCollOpen(true);
    setCollections(null);
    try { setCollections(await listCollections()); } catch { setCollections([]); }
  }

  async function pickCollection(c: Collection) {
    try {
      await addToCollection(c.id, id);
      flash(`Added to “${c.name}”`);
    } catch (err: any) {
      if (err?.message !== "unauthorized") flash("Could not add to collection");
    } finally {
      setCollOpen(false);
    }
  }

  async function doShare() {
    setShareBusy(true);
    try {
      const s = await createShare({ scope_type: "asset", scope_id: id, permission: sharePerm, watermark: shareWm });
      setShare(s);
    } catch (err: any) {
      if (err?.message !== "unauthorized") flash("Could not create share");
    } finally {
      setShareBusy(false);
    }
  }

  function copyShare() {
    if (!share?.url) return;
    navigator.clipboard?.writeText(share.url).then(() => flash("Link copied"), () => {});
  }

  async function reprocess() {
    try {
      await reprocessAsset(id);
      flash("Reprocessing queued");
    } catch (err: any) {
      if (err?.message !== "unauthorized") flash("Reprocess failed");
    }
  }

  async function remove() {
    if (!confirm("Delete this asset? It will be moved to trash.")) return;
    try {
      await deleteAsset(id);
      router.push("/explorer");
    } catch (err: any) {
      if (err?.message !== "unauthorized") flash("Delete failed");
    }
  }

  function set<K extends keyof typeof form>(k: K, v: string) {
    setForm((f) => ({ ...f, [k]: v }));
  }

  if (loading) {
    return (
      <AppShell subtitle="Asset">
        <div className="empty"><span className="spinner" /> Loading…</div>
      </AppShell>
    );
  }
  if (!asset) {
    return (
      <AppShell subtitle="Asset">
        <div className="empty">Asset not found.</div>
      </AppShell>
    );
  }

  const hasTranscript = asset.transcript?.length > 0;
  const hasMarkers = asset.markers?.length > 0;
  const wfClass = WF_BADGE[asset.workflow] || "";

  return (
    <AppShell subtitle={asset.filename}>
      <button
        onClick={() => {
          // Return to where the user came from (search results keep their ?q= in the URL,
          // so back restores the exact query). Fall back to Search if opened directly.
          if (typeof window !== "undefined" && window.history.length > 1) router.back();
          else router.push("/search");
        }}
        style={{ background: "transparent", border: "1px solid var(--border)", color: "var(--muted)",
                 borderRadius: 8, padding: "6px 14px", cursor: "pointer", marginBottom: 14, fontSize: 13 }}
      >
        ← Back
      </button>
      <div className="viewer">
        {/* LEFT */}
        <div>
          <div className="stage">
            {asset.type === "image" && url && <img src={url} alt={asset.title || asset.filename} />}
            {asset.type === "video" && url && <video ref={mediaRef} src={url} controls />}
            {asset.type === "audio" && url && <audio ref={mediaRef} src={url} controls />}
            {asset.type === "document" && (
              // In-app reader: renders the EXTRACTED TEXT with find-in-document. Reliable for both
              // PDF and DOCX (the browser's iframe PDF plugin renders many presigned PDFs blank);
              // the visual original is one click away via the reader's "Open original ↗".
              docText
                ? <DocReader doc={docText} originalUrl={url} initialQuery={qs.get("find") || ""} />
                : <div style={{ textAlign: "center", padding: 40 }}>
                    <div style={{ fontSize: 64 }}>{TYPE_ICON.document}</div>
                    <div className="muted" style={{ marginTop: 10 }}>Loading document…</div>
                  </div>
            )}
            {!url && asset.type !== "document" && <span className="muted">Preview unavailable</span>}
          </div>

          <div className="panel" style={{ marginTop: 18 }}>
            <div className="tabs">
              {hasTranscript && (
                <div className={`tab ${tab === "transcript" ? "active" : ""}`} onClick={() => setTab("transcript")}>Transcript</div>
              )}
              {hasMarkers && (
                <div className={`tab ${tab === "detections" ? "active" : ""}`} onClick={() => setTab("detections")}>Detections</div>
              )}
              <div className={`tab ${tab === "metadata" ? "active" : ""}`} onClick={() => setTab("metadata")}>Metadata</div>
              <div className={`tab ${tab === "workflow" ? "active" : ""}`} onClick={() => setTab("workflow")}>Workflow</div>
            </div>

            {/* Transcript */}
            {tab === "transcript" && (
              hasTranscript ? (
                <div>
                  {asset.transcript.map((seg: Transcript) => (
                    <div className="seg" key={seg.id} onClick={() => seek(seg.start_seconds)} title="Click to seek">
                      <span className="t">{fmtClock(seg.start_seconds)}</span>
                      {seg.speaker && <b>{seg.speaker}: </b>}
                      {seg.text}
                    </div>
                  ))}
                </div>
              ) : <div className="empty">No transcript.</div>
            )}

            {/* Detections */}
            {tab === "detections" && (
              hasMarkers ? (
                <div>
                  {asset.markers.map((m: Marker) => (
                    <div className="seg" key={m.id} onClick={() => seek(m.start_seconds)} title="Click to seek">
                      <span className="t">{m.smpte || (m.frame_index != null ? `#${m.frame_index}` : "")}</span>
                      <b>{m.kind}</b>
                      {m.label ? `: ${m.label}` : ""}
                      {m.person_id ? ` (person ${String(m.person_id).slice(0, 8)})` : ""}
                    </div>
                  ))}
                </div>
              ) : <div className="empty">No detections.</div>
            )}

            {/* Metadata */}
            {tab === "metadata" && (
              <div>
                <div className="field-group">
                  <label className="lbl">Title</label>
                  <input className="field" value={form.title} onChange={(e) => set("title", e.target.value)} />
                </div>
                <div className="field-group">
                  <label className="lbl">Description</label>
                  <textarea className="field" rows={3} value={form.description} onChange={(e) => set("description", e.target.value)} />
                </div>
                <div className="field-group">
                  <label className="lbl">Tags (comma separated)</label>
                  <input className="field" value={form.tags} onChange={(e) => set("tags", e.target.value)} />
                </div>
                <div className="field-group">
                  <label className="lbl">Department</label>
                  <input className="field" value={form.department} onChange={(e) => set("department", e.target.value)} />
                </div>
                <div className="field-group">
                  <label className="lbl">Project</label>
                  <input className="field" value={form.project} onChange={(e) => set("project", e.target.value)} />
                </div>
                <div className="field-group">
                  <label className="lbl">Rights</label>
                  <input className="field" value={form.rights} onChange={(e) => set("rights", e.target.value)} />
                </div>
                <div className="field-group">
                  <label className="lbl">Language</label>
                  <input className="field" value={form.language} onChange={(e) => set("language", e.target.value)} />
                </div>
                <button className="btn" onClick={save} disabled={saving}>
                  {saving ? <span className="spinner" /> : "Save"}
                </button>
              </div>
            )}

            {/* Workflow */}
            {tab === "workflow" && (
              <div>
                <div className="kv" style={{ marginBottom: 14 }}>
                  <b>Current: </b>
                  <span className={`badge ${wfClass}`}>{asset.workflow}</span>
                </div>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 18 }}>
                  {WF_ACTIONS.map((a) => (
                    <button
                      key={a.state}
                      className="btn ghost sm"
                      disabled={busy || asset.workflow === a.state}
                      onClick={() => transition(a.state)}
                    >
                      {a.label}
                    </button>
                  ))}
                </div>
                {history.length > 0 ? (
                  <div className="tbl">
                    {history.map((h, i) => (
                      <div className="row" key={i}>
                        <span className={`badge ${WF_BADGE[h.state] || ""}`}>{h.state}</span>
                        <span className="muted" style={{ marginLeft: "auto", fontSize: 12 }}>
                          {new Date(h.created_at).toLocaleString()}
                        </span>
                      </div>
                    ))}
                  </div>
                ) : <div className="muted" style={{ fontSize: 13 }}>No workflow history.</div>}
              </div>
            )}
          </div>
        </div>

        {/* RIGHT */}
        <div>
          <div className="panel">
            <h3>Details</h3>
            <div className="kv"><b>Type:</b> {asset.type}</div>
            <div className="kv"><b>Status:</b> {asset.status}</div>
            {asset.status === "failed" && asset.error_detail && (
              // WHY it failed — so the user can fix the cause (corrupt file, unsupported
              // codec…) instead of staring at a bare "failed" badge.
              <div className="kv" style={{ color: "var(--red, #e5484d)" }}>
                <b>Error:</b> {asset.error_detail}
              </div>
            )}
            <div className="kv"><b>Language:</b> {asset.language || "—"}</div>
            <div className="kv"><b>Department:</b> {asset.department || "—"}</div>
            <div className="kv"><b>Project:</b> {asset.project || "—"}</div>
            <div className="kv"><b>Tags:</b> {asset.tags?.length ? asset.tags.join(", ") : "—"}</div>
            <div className="kv"><b>Workflow:</b> {asset.workflow}</div>
            <div className="kv"><b>Size:</b> {fmtBytes(asset.size_bytes)}</div>

            <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 16 }}>
              {url && (
                // A DAM's core job: let the user take the original home.
                <a className="btn ghost" href={url} target="_blank" rel="noreferrer"
                   download={asset.filename} title="Download the original file">
                  Download
                </a>
              )}
              <button className="btn ghost" onClick={openCollections}>Add to collection</button>
              <button className="btn ghost" onClick={() => { setShare(null); setShareOpen(true); }}>Share</button>
              <button className="btn ghost" onClick={reprocess}>Reprocess</button>
              <button className="btn danger" onClick={remove}>Delete</button>
            </div>
          </div>
        </div>
      </div>

      {/* Add-to-collection modal */}
      {collOpen && (
        <div className="modal-bg" onClick={() => setCollOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>Add to collection</h3>
            {collections === null ? (
              <div className="empty"><span className="spinner" /> Loading…</div>
            ) : collections.length === 0 ? (
              <div className="empty">No collections yet.</div>
            ) : (
              <div className="tbl">
                {collections.map((c) => (
                  <div className="row" key={c.id} style={{ cursor: "pointer" }} onClick={() => pickCollection(c)}>
                    <span>{c.name}</span>
                    <span className="muted" style={{ marginLeft: "auto", fontSize: 12 }}>{c.item_count} items</span>
                  </div>
                ))}
              </div>
            )}
            <div style={{ marginTop: 16, textAlign: "right" }}>
              <button className="btn ghost sm" onClick={() => setCollOpen(false)}>Close</button>
            </div>
          </div>
        </div>
      )}

      {/* Share modal */}
      {shareOpen && (
        <div className="modal-bg" onClick={() => setShareOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>Share asset</h3>
            {share ? (
              <div>
                <div className="field-group">
                  <label className="lbl">Share link</label>
                  <input className="field" readOnly value={share.url} onFocus={(e) => e.currentTarget.select()} />
                </div>
                <div style={{ display: "flex", gap: 8 }}>
                  <button className="btn" onClick={copyShare}>Copy link</button>
                  <button className="btn ghost" onClick={() => setShareOpen(false)}>Done</button>
                </div>
              </div>
            ) : (
              <div>
                <div className="field-group">
                  <label className="lbl">Permission</label>
                  <select className="field" value={sharePerm} onChange={(e) => setSharePerm(e.target.value)}>
                    <option value="view">View</option>
                    <option value="download">Download</option>
                  </select>
                </div>
                <div className="field-group">
                  <label className="lbl" style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
                    <input type="checkbox" checked={shareWm} onChange={(e) => setShareWm(e.target.checked)} />
                    Apply watermark
                  </label>
                </div>
                <div style={{ display: "flex", gap: 8 }}>
                  <button className="btn" onClick={doShare} disabled={shareBusy}>
                    {shareBusy ? <span className="spinner" /> : "Create link"}
                  </button>
                  <button className="btn ghost" onClick={() => setShareOpen(false)}>Cancel</button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {toast && <div className="toast">{toast}</div>}
    </AppShell>
  );
}
