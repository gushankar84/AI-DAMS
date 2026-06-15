"use client";
import { useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import AssetCard from "@/components/AssetCard";
import {
  listCollections,
  getCollection,
  createCollection,
  removeFromCollection,
  type Collection,
  type Asset,
} from "@/lib/api";

type OpenCollection = { id: string; name: string; description: string | null; assets: Asset[] };

export default function CollectionsPage() {
  const [collections, setCollections] = useState<Collection[] | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [open, setOpen] = useState<OpenCollection | null>(null);
  const [openLoading, setOpenLoading] = useState(false);
  const [showNew, setShowNew] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function loadCollections() {
    try {
      const data = await listCollections();
      setCollections(data);
    } catch (e: any) {
      if (e.message === "unauthorized") return;
      setErr(e.message || "Failed to load collections");
      setCollections([]);
    }
  }

  useEffect(() => {
    loadCollections();
  }, []);

  async function loadOpen(id: string) {
    setOpenLoading(true);
    try {
      const data = await getCollection(id);
      setOpen(data);
    } catch (e: any) {
      if (e.message === "unauthorized") return;
      setErr(e.message || "Failed to load collection");
      setOpen(null);
    } finally {
      setOpenLoading(false);
    }
  }

  useEffect(() => {
    if (openId) loadOpen(openId);
    else setOpen(null);
  }, [openId]);

  async function submitNew(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setSaving(true);
    setErr(null);
    try {
      await createCollection(name.trim(), description.trim() || undefined);
      setShowNew(false);
      setName("");
      setDescription("");
      await loadCollections();
    } catch (e: any) {
      if (e.message === "unauthorized") return;
      setErr(e.message || "Failed to create collection");
    } finally {
      setSaving(false);
    }
  }

  async function remove(assetId: string) {
    if (!openId) return;
    if (!confirm("Remove this asset from the collection? (The asset itself is not deleted.)")) return;
    try {
      await removeFromCollection(openId, assetId);
      await loadOpen(openId);
      await loadCollections();
    } catch (e: any) {
      if (e.message === "unauthorized") return;
      setErr(e.message || "Failed to remove asset");
    }
  }

  // ── Open collection detail view ──────────────────────────────
  if (openId) {
    return (
      <AppShell title="Collections" subtitle="Virtual folders — group mixed assets without duplication">
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 18 }}>
          <button className="btn ghost sm" onClick={() => setOpenId(null)}>← Back</button>
          <h3 style={{ margin: 0 }}>{open?.name || "Collection"}</h3>
        </div>

        {err && <div className="err">{err}</div>}

        {openLoading && !open ? (
          <div className="empty"><span className="spinner" /> Loading…</div>
        ) : !open || open.assets.length === 0 ? (
          <div className="empty">This collection is empty. Add assets from the explorer or search.</div>
        ) : (
          <div className="grid">
            {open.assets.map((a) => (
              <div key={a.id}>
                <AssetCard
                  id={a.id}
                  type={a.type}
                  title={a.title}
                  filename={a.filename}
                  thumbnailUri={a.thumbnail_uri}
                  signals={a.tags}
                />
                <button
                  className="btn sm danger"
                  style={{ marginTop: 8, width: "100%", justifyContent: "center" }}
                  onClick={() => remove(a.id)}
                >
                  Remove
                </button>
              </div>
            ))}
          </div>
        )}
      </AppShell>
    );
  }

  // ── List view ────────────────────────────────────────────────
  return (
    <AppShell title="Collections" subtitle="Virtual folders — group mixed assets without duplication">
      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 18 }}>
        <button className="btn" onClick={() => { setErr(null); setShowNew(true); }}>＋ New collection</button>
      </div>

      {err && !showNew && <div className="err">{err}</div>}

      {collections === null ? (
        <div className="empty"><span className="spinner" /> Loading…</div>
      ) : collections.length === 0 ? (
        <div className="empty">No collections yet. Create one to group assets without duplicating them.</div>
      ) : (
        <div className="grid">
          {collections.map((c) => (
            <div key={c.id} className="panel" style={{ cursor: "pointer" }} onClick={() => setOpenId(c.id)}>
              <h3>{c.name}</h3>
              {c.description && <p className="muted" style={{ margin: "0 0 12px", fontSize: 13 }}>{c.description}</p>}
              <span className="badge">{c.item_count} item{c.item_count === 1 ? "" : "s"}</span>
            </div>
          ))}
        </div>
      )}

      {showNew && (
        <div className="modal-bg" onClick={() => !saving && setShowNew(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>New collection</h3>
            <form onSubmit={submitNew}>
              {err && <div className="err">{err}</div>}
              <div className="field-group">
                <label className="lbl">Name</label>
                <input
                  className="field"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. Q3 Campaign Hero Shots"
                  autoFocus
                />
              </div>
              <div className="field-group">
                <label className="lbl">Description</label>
                <textarea
                  className="field"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="Optional — what belongs in this collection"
                  rows={3}
                />
              </div>
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
                <button type="button" className="btn ghost" onClick={() => setShowNew(false)} disabled={saving}>Cancel</button>
                <button type="submit" className="btn" disabled={saving || !name.trim()}>
                  {saving ? <span className="spinner" /> : "Create"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </AppShell>
  );
}
