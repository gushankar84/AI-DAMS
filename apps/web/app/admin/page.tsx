"use client";
import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import {
  listUsers, createUser, listPersons, updatePerson, generatePersonFace,
  mergePersons, splitPerson, personSuggestions, personAssets,
  adminQueue, adminModels, reprocessFailed,
  type User, type Person, type MergeSuggestion, type AssetType,
} from "@/lib/api";

type Tab = "users" | "people" | "processing" | "models";
const TABS: { key: Tab; label: string }[] = [
  { key: "users", label: "Users" },
  { key: "people", label: "People & Consent" },
  { key: "processing", label: "Processing" },
  { key: "models", label: "Models" },
];

const ROLES = ["viewer", "contributor", "reviewer", "distributor", "administrator"];
const CONSENT = ["unknown", "granted", "denied", "revoked"];

// Map an arbitrary status string to a badge colour variant.
function statusBadge(s: string): string {
  const v = (s || "").toLowerCase();
  if (["ready", "completed", "done", "indexed", "success", "active"].includes(v)) return "badge green";
  if (["failed", "error", "errored", "dead"].includes(v)) return "badge red";
  if (["processing", "queued", "pending", "running", "retry", "waiting"].includes(v)) return "badge amber";
  return "badge";
}
function consentBadge(s: string): string {
  const v = (s || "").toLowerCase();
  if (v === "granted") return "badge green";
  if (v === "denied" || v === "revoked") return "badge red";
  return "badge amber";
}

type QueueJob = { id?: string; asset_id?: string; title?: string | null; filename?: string | null; type?: string; status?: string; error?: string | null };
type QueueData = { by_status?: Record<string, number>; counts?: Record<string, number>; queue?: Record<string, number>; active?: QueueJob[]; items?: QueueJob[]; jobs?: QueueJob[] };
type ModelsData = {
  configured?: { text_embed?: string; reranker?: string; image_embed?: string; asr?: string };
  model_server?: { reachable?: boolean; capabilities?: Record<string, boolean> } | null;
  // fallback/legacy shapes
  text_embed?: string; reranker?: string; image_embed?: string; asr?: string;
  models?: Record<string, string>;
  server?: { reachable?: boolean; capabilities?: Record<string, boolean> } | null;
  reachable?: boolean; capabilities?: Record<string, boolean>;
};

export default function Admin() {
  const [tab, setTab] = useState<Tab>("users");

  return (
    <AppShell title="Administration">
      <div className="tabs">
        {TABS.map((t) => (
          <div key={t.key} className={`tab ${tab === t.key ? "active" : ""}`} onClick={() => setTab(t.key)}>
            {t.label}
          </div>
        ))}
      </div>

      {tab === "users" && <UsersTab />}
      {tab === "people" && <PeopleTab />}
      {tab === "processing" && <ProcessingTab />}
      {tab === "models" && <ModelsTab />}
    </AppShell>
  );
}

// ─── Users ──────────────────────────────────────────────────────────────────
function UsersTab() {
  const [users, setUsers] = useState<User[] | null>(null);
  const [showModal, setShowModal] = useState(false);

  const load = useCallback(async () => {
    setUsers(null);
    try {
      setUsers(await listUsers());
    } catch (err: any) {
      if (err.message === "unauthorized") return;
      setUsers([]);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="panel">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
        <h3 style={{ margin: 0 }}>Users</h3>
        <button className="btn sm" onClick={() => setShowModal(true)}>＋ Add user</button>
      </div>

      {users === null ? (
        <div className="empty"><span className="spinner" /></div>
      ) : users.length === 0 ? (
        <div className="empty">No users yet.</div>
      ) : (
        <table className="tbl">
          <thead>
            <tr><th>Email</th><th>Name</th><th>Role</th><th>Active</th></tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id}>
                <td>{u.email}</td>
                <td>{u.display_name}</td>
                <td><span className="badge type">{u.role}</span></td>
                <td><span className={u.is_active ? "badge green" : "badge red"}>{u.is_active ? "active" : "disabled"}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {showModal && <AddUserModal onClose={() => setShowModal(false)} onCreated={() => { setShowModal(false); load(); }} />}
    </div>
  );
}

function AddUserModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("viewer");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await createUser({ email, display_name: displayName, password, role });
      onCreated();
    } catch (err: any) {
      if (err.message === "unauthorized") { onClose(); return; }
      setError(err.message || "Could not create user.");
      setBusy(false);
    }
  }

  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>Add user</h3>
        <form onSubmit={submit}>
          {error && <div className="err">{error}</div>}
          <div className="field-group">
            <label className="lbl">Email</label>
            <input className="field" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          <div className="field-group">
            <label className="lbl">Display name</label>
            <input className="field" required value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
          </div>
          <div className="field-group">
            <label className="lbl">Password</label>
            <input className="field" type="password" required value={password} onChange={(e) => setPassword(e.target.value)} />
          </div>
          <div className="field-group">
            <label className="lbl">Role</label>
            <select className="field" value={role} onChange={(e) => setRole(e.target.value)}>
              {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          </div>
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 18 }}>
            <button type="button" className="btn ghost" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn" disabled={busy}>{busy ? "Creating…" : "Create user"}</button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ─── People & Consent ─────────────────────────────────────────────────────────
// A cluster's representative face. The crop is generated lazily server-side: if the
// avatar doesn't exist yet the <img> 404s, we POST to generate it, then show it.
function PersonFace({ person, size = 46 }: { person: Person; size?: number }) {
  const [src, setSrc] = useState(person.thumb_url || "");
  const [tried, setTried] = useState(false);
  const box: React.CSSProperties = { width: size, height: size, borderRadius: 8, objectFit: "cover",
    background: "var(--panel-2)", display: "flex", alignItems: "center", justifyContent: "center",
    fontSize: Math.round(size / 2), flex: "none" };
  if (!src) return <div style={box}>🧑</div>;
  return (
    <img src={src} alt="face" style={box}
      onError={async () => {
        if (tried) { setSrc(""); return; }
        setTried(true);
        try { const r = await generatePersonFace(person.id); setSrc(r.thumb_url); }
        catch { setSrc(""); }
      }} />
  );
}

function PeopleTab() {
  const [persons, setPersons] = useState<Person[] | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [sugg, setSugg] = useState<Record<string, MergeSuggestion>>({});
  const [busy, setBusy] = useState(false);
  const [photos, setPhotos] = useState<{ person: Person; items: { id: string; filename: string; type: AssetType; face_url: string | null }[] } | null>(null);

  const load = useCallback(async () => {
    setPersons(null); setSelected(new Set());
    try {
      setPersons(await listPersons());
    } catch (err: any) {
      if (err.message === "unauthorized") return;
      setPersons([]);
    }
  }, []);

  // Load people, then auto-run look-alike detection (no need to click the button).
  useEffect(() => { load().then(() => { loadSuggestions().catch(() => {}); }); }, [load]);

  async function openPhotos(p: Person) {
    try { setPhotos({ person: p, items: await personAssets(p.id) }); } catch { /* ignore */ }
  }

  // Un-merge: split a cluster that actually holds two people into two identities.
  async function doSplit(p: Person) {
    if (!window.confirm(`Split "${p.display_name || "this cluster"}" into two people?\n` +
        `The faces are auto-grouped into two; re-merge if it's wrong.`)) return;
    setBusy(true);
    try { await splitPerson(p.id); setPhotos(null); await load(); await loadSuggestions(); }
    catch { window.alert("Couldn't split — needs at least 4 faces and two separable groups."); }
    finally { setBusy(false); }
  }

  async function commitName(p: Person, name: string) {
    try {
      await updatePerson(p.id, { display_name: name });
      setPersons((cur) => cur ? cur.map((x) => x.id === p.id ? { ...x, display_name: name } : x) : cur);
    } catch { /* leave field for retry */ }
  }

  // Suggest the next free variant of a taken name, e.g. "Nani" -> "Nani2".
  function uniqueName(base: string): string {
    const taken = new Set((persons || []).map((x) => (x.display_name || "").trim().toLowerCase()));
    let n = 2, cand = `${base}${n}`;
    while (taken.has(cand.toLowerCase())) { n++; cand = `${base}${n}`; }
    return cand;
  }

  // Names are UNIQUE labels (the face is the real identity). If a name is already taken by
  // another cluster, either it's the same person (merge) or a different one (needs Nani2).
  async function saveName(p: Person, value: string) {
    const next = value.trim();
    if (next === (p.display_name || "")) return;
    if (!next) { await commitName(p, ""); return; }
    const clash = (persons || []).find(
      (x) => x.id !== p.id && (x.display_name || "").trim().toLowerCase() === next.toLowerCase());
    if (clash) {
      const same = window.confirm(
        `"${clash.display_name}" already exists.\n\nIs this the SAME person?\n` +
        `OK  →  merge them into one identity.\nCancel  →  a different person (give a unique name).`);
      if (same) {
        setBusy(true);
        try { await mergePersons(clash.id, [p.id]); await load(); await loadSuggestions(); }
        finally { setBusy(false); }
        return;
      }
      const chosen = window.prompt(`"${next}" is already used. Enter a unique name:`, uniqueName(next));
      if (!chosen || !chosen.trim()) { await load(); return; }  // cancelled → revert the field
      await commitName(p, chosen.trim());
      return;
    }
    await commitName(p, next);
  }

  async function saveConsent(p: Person, value: string) {
    try {
      await updatePerson(p.id, { consent_status: value });
      setPersons((cur) => cur ? cur.map((x) => x.id === p.id ? { ...x, consent_status: value } : x) : cur);
    } catch { /* ignore */ }
  }

  function toggle(id: string) {
    setSelected((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  }

  async function loadSuggestions() {
    const map: Record<string, MergeSuggestion> = {};
    for (const x of await personSuggestions()) map[x.person_id] = x;
    setSugg(map);
  }

  // Merge keeps the cluster with the MOST faces (best-established) as the survivor, then
  // RE-SCANS — so naming/merging one person cascades to find their other split clusters.
  async function mergeIds(ids: string[]) {
    if (!persons || ids.length < 2) return;
    const chosen = persons.filter((p) => ids.includes(p.id)).sort((a, b) => b.face_count - a.face_count);
    setBusy(true);
    try {
      await mergePersons(chosen[0].id, chosen.slice(1).map((p) => p.id));
      await load();
      await loadSuggestions();   // "try similarity in other places" after every merge
    } catch { /* ignore */ } finally { setBusy(false); }
  }

  async function findDupes() {
    setBusy(true);
    try { await loadSuggestions(); } catch { /* ignore */ } finally { setBusy(false); }
  }

  return (
    <div className="panel">
      <h3>People &amp; Consent</h3>
      <p className="muted" style={{ marginTop: -6, marginBottom: 12 }}>
        Facial recognition is consent-gated; denied/revoked persons are excluded from search.
        Same person split across rows? Tick them and Merge, or use “Find look-alikes”.
      </p>

      <div style={{ display: "flex", gap: 8, marginBottom: 14, alignItems: "center" }}>
        <button className="btn ghost" onClick={findDupes} disabled={busy}>Find look-alikes</button>
        {selected.size >= 2 && (
          <button className="btn" onClick={() => mergeIds([...selected])} disabled={busy}>
            Merge {selected.size} selected
          </button>
        )}
        {busy && <span className="spinner" />}
      </div>

      {persons === null ? (
        <div className="empty"><span className="spinner" /></div>
      ) : persons.length === 0 ? (
        <div className="empty">No people detected yet.</div>
      ) : (
        <table className="tbl">
          <thead>
            <tr><th></th><th>Face</th><th>ID</th><th>Faces</th><th>Name</th><th>Consent</th><th>Looks like</th></tr>
          </thead>
          <tbody>
            {persons.map((p) => {
              const s = sugg[p.id];
              const other = s ? persons.find((x) => x.id === s.similar_to) : undefined;
              return (
                <tr key={p.id}>
                  <td><input type="checkbox" checked={selected.has(p.id)} onChange={() => toggle(p.id)} /></td>
                  <td>
                    <div onClick={() => openPhotos(p)} style={{ cursor: "pointer" }}
                         title="Click to see this person's photos">
                      <PersonFace person={p} />
                    </div>
                  </td>
                  <td className="muted">{p.id.slice(0, 8)}</td>
                  <td>{p.face_count}</td>
                  <td>
                    <input
                      className="field"
                      key={p.display_name || ""}
                      defaultValue={p.display_name || ""}
                      placeholder="Unnamed"
                      onBlur={(e) => saveName(p, e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
                    />
                  </td>
                  <td>
                    <select className="field" value={p.consent_status} onChange={(e) => saveConsent(p, e.target.value)}>
                      {CONSENT.map((c) => <option key={c} value={c}>{c}</option>)}
                    </select>
                  </td>
                  <td style={{ fontSize: 12 }}>
                    {s && other ? (
                      <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                        {/* show the look-alike's FACE (recognisable), never a raw ID hash */}
                        <PersonFace person={other} size={28} />
                        {other.display_name ? (
                          <>
                            <span className="muted">{other.display_name} ({s.score})</span>
                            {!p.display_name && (
                              <button className="chip" onClick={() => saveName(p, other.display_name!)}>use name</button>
                            )}
                          </>
                        ) : (
                          // neither named → offer to name this person right here
                          <span className="muted">similar ({s.score})</span>
                        )}
                        <button className="chip" onClick={() => mergeIds([p.id, s.similar_to])} disabled={busy}>merge</button>
                        {!other.display_name && !p.display_name && (
                          <button
                            className="chip"
                            onClick={() => {
                              const n = window.prompt("Name this person:");
                              if (n && n.trim()) saveName(p, n.trim());
                            }}
                          >name</button>
                        )}
                      </div>
                    ) : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {photos && (
        <div onClick={() => setPhotos(null)}
             style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.65)", zIndex: 50,
                      display: "flex", alignItems: "center", justifyContent: "center", padding: 24 }}>
          <div onClick={(e) => e.stopPropagation()}
               style={{ background: "var(--bg-2)", border: "1px solid var(--border)", borderRadius: 12,
                        padding: 20, maxWidth: "82vw", maxHeight: "84vh", overflow: "auto", minWidth: 360 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14, gap: 16 }}>
              <strong>{photos.person.display_name || photos.person.id.slice(0, 8)} — {photos.items.length} photo(s)</strong>
              <div style={{ display: "flex", gap: 8 }}>
                <button className="btn ghost" onClick={() => doSplit(photos.person)} disabled={busy}
                        title="If this cluster actually contains two different people, split it into two">
                  Split into 2
                </button>
                <button className="btn ghost" onClick={() => setPhotos(null)}>Close</button>
              </div>
            </div>
            {photos.items.length === 0 ? (
              <div className="empty">No photos.</div>
            ) : (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 14 }}>
                {photos.items.map((it) => (
                  <div key={it.id} onClick={() => window.open(`/asset/${it.id}`, "_blank")}
                       style={{ cursor: "pointer", textAlign: "center", width: 150 }}
                       title={`Open ${it.filename}`}>
                    {it.face_url ? (
                      // enlarged CROPPED FACE — not the full frame — to verify identity
                      <img src={it.face_url} alt={it.filename}
                           style={{ width: 150, height: 150, objectFit: "cover", borderRadius: 10,
                                    border: "1px solid var(--border)", background: "var(--panel-2)" }} />
                    ) : (
                      <div style={{ width: 150, height: 150, borderRadius: 10, background: "var(--panel-2)",
                                    display: "flex", alignItems: "center", justifyContent: "center", fontSize: 40 }}>🧑</div>
                    )}
                    <div className="muted" style={{ fontSize: 11, marginTop: 6, overflow: "hidden",
                                                     textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{it.filename}</div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Processing ────────────────────────────────────────────────────────────────
function ProcessingTab() {
  const [data, setData] = useState<QueueData | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setData(null);
    try {
      setData((await adminQueue()) as QueueData);
    } catch (err: any) {
      if (err.message === "unauthorized") return;
      setData({});
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function reprocess() {
    setBusy(true);
    try {
      await reprocessFailed();
      await load();
    } catch (err: any) {
      if (err.message !== "unauthorized") setBusy(false);
      return;
    }
    setBusy(false);
  }

  if (data === null) return <div className="panel"><div className="empty"><span className="spinner" /></div></div>;

  const counts = data.by_status || data.counts || data.queue || {};
  const jobs = data.active || data.items || data.jobs || [];
  const countEntries = Object.entries(counts);

  return (
    <>
      {countEntries.length > 0 && (
        <div className="tiles">
          {countEntries.map(([k, n]) => (
            <div className="tile" key={k}>
              <div className="n">{n}</div>
              <div className="l"><span className={statusBadge(k)}>{k}</span></div>
            </div>
          ))}
        </div>
      )}

      <div className="panel">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
          <h3 style={{ margin: 0 }}>Active processing</h3>
          <button className="btn danger sm" onClick={reprocess} disabled={busy}>
            {busy ? "Reprocessing…" : "Reprocess failed"}
          </button>
        </div>

        {jobs.length === 0 ? (
          <div className="empty">No assets in the processing queue.</div>
        ) : (
          <table className="tbl">
            <thead>
              <tr><th>Title</th><th>Type</th><th>Status</th><th>Error</th></tr>
            </thead>
            <tbody>
              {jobs.map((j, i) => (
                <tr key={j.id || j.asset_id || i}>
                  <td>{j.title || j.filename || j.asset_id || "—"}</td>
                  <td>{j.type || "—"}</td>
                  <td>{j.status ? <span className={statusBadge(j.status)}>{j.status}</span> : "—"}</td>
                  <td className="muted">{j.error || ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}

// ─── Models ──────────────────────────────────────────────────────────────────
function ModelsTab() {
  const [data, setData] = useState<ModelsData | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = (await adminModels()) as ModelsData;
        if (alive) setData(res);
      } catch (err: any) {
        if (err.message === "unauthorized") return;
        if (alive) setData({});
      }
    })();
    return () => { alive = false; };
  }, []);

  if (data === null) return <div className="panel"><div className="empty"><span className="spinner" /></div></div>;

  const m = data.models || {};
  const c = data.configured || {};
  const rows: { label: string; value: string | undefined }[] = [
    { label: "Text embedding", value: c.text_embed ?? data.text_embed ?? m.text_embed },
    { label: "Reranker", value: c.reranker ?? data.reranker ?? m.reranker },
    { label: "Image embedding", value: c.image_embed ?? data.image_embed ?? m.image_embed },
    { label: "Speech-to-text (ASR)", value: c.asr ?? data.asr ?? m.asr },
  ];
  const reachable = data.model_server?.reachable ?? data.server?.reachable ?? data.reachable;
  const capabilities = data.model_server?.capabilities ?? data.server?.capabilities ?? data.capabilities ?? {};
  const caps = ["torch", "sentence_transformers", "open_clip"];

  return (
    <div className="panel">
      <h3>Configured models</h3>
      {rows.map((r) => (
        <div className="kv" key={r.label}><b>{r.label}:</b> {r.value || "not configured"}</div>
      ))}

      <h3 style={{ marginTop: 22 }}>Model server</h3>
      <div className="kv">
        <b>Status:</b>{" "}
        <span className={reachable ? "badge green" : "badge red"}>{reachable ? "reachable" : "unreachable"}</span>
      </div>
      <div className="badges" style={{ marginTop: 10 }}>
        {caps.map((c) => (
          <span key={c} className={capabilities[c] ? "badge green" : "badge red"}>{c}</span>
        ))}
      </div>
    </div>
  );
}
