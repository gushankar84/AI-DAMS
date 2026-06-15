"use client";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { listAssets, deleteAsset, restoreAsset, TYPE_ICON, type Asset } from "@/lib/api";
import AppShell from "@/components/AppShell";
import AssetCard from "@/components/AssetCard";

const NAV: { key: string; label: string }[] = [
  { key: "", label: "All" },
  { key: "document", label: "Documents" },
  { key: "image", label: "Images" },
  { key: "video", label: "Videos" },
  { key: "audio", label: "Audio" },
];

export default function ExplorerPage() {
  const router = useRouter();
  const [filter, setFilter] = useState<string>("");
  const [view, setView] = useState<"grid" | "list">("grid");
  const [assets, setAssets] = useState<Asset[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setSelected(new Set());
    try {
      const rows =
        filter === "trash"
          ? await listAssets({ trashed: true, limit: 100 })
          : await listAssets({ type: filter || undefined, limit: 100 });
      setAssets(rows);
    } catch (err: any) {
      if (err?.message === "unauthorized") return;
      setError(err?.message || "Failed to load assets.");
      setAssets([]);
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    load();
  }, [load]);

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function bulkAction() {
    if (selected.size === 0 || busy) return;
    setBusy(true);
    const ids = Array.from(selected);
    try {
      for (const id of ids) {
        if (filter === "trash") await restoreAsset(id);
        else await deleteAsset(id);
      }
      await load();
    } catch (err: any) {
      if (err?.message === "unauthorized") return;
      setError(err?.message || "Bulk action failed.");
    } finally {
      setBusy(false);
    }
  }

  const inTrash = filter === "trash";

  return (
    <AppShell title="Assets" subtitle="Browse your library">
      <div style={{ display: "flex", gap: 20, alignItems: "flex-start" }}>
        <nav style={{ width: 170, flex: "0 0 170px" }}>
          <div className="panel">
            {NAV.map((n) => (
              <div
                key={n.key || "all"}
                className={`navlink ${filter === n.key ? "active" : ""}`}
                onClick={() => setFilter(n.key)}
              >
                <span className="ico">{n.key ? TYPE_ICON[n.key] || "📁" : "▦"}</span>
                {n.label}
              </div>
            ))}
            <div style={{ borderTop: "1px solid var(--border)", margin: "8px 0" }} />
            <div
              className={`navlink ${inTrash ? "active" : ""}`}
              onClick={() => setFilter("trash")}
            >
              <span className="ico">🗑</span>Trash
            </div>
            <div className="navlink" onClick={() => router.push("/collections")}>
              <span className="ico">📚</span>Collections
            </div>
            <div className="navlink" onClick={() => router.push("/distribution")}>
              <span className="ico">🔗</span>Shared
            </div>
          </div>
        </nav>

        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="filters" style={{ justifyContent: "space-between" }}>
            <div className="filters" style={{ padding: 0 }}>
              <button
                className={`chip ${view === "grid" ? "active" : ""}`}
                onClick={() => setView("grid")}
              >
                ▦ Grid
              </button>
              <button
                className={`chip ${view === "list" ? "active" : ""}`}
                onClick={() => setView("list")}
              >
                ☰ List
              </button>
            </div>
          </div>

          {selected.size > 0 && (
            <div className="filters" style={{ alignItems: "center" }}>
              <span className="muted">{selected.size} selected</span>
              <button
                className={inTrash ? "btn sm" : "btn danger sm"}
                disabled={busy}
                onClick={bulkAction}
              >
                {busy ? "Working…" : inTrash ? "Restore" : "Delete"}
              </button>
              <button className="btn ghost sm" disabled={busy} onClick={() => setSelected(new Set())}>
                Clear
              </button>
            </div>
          )}

          {error && <div className="err">{error}</div>}

          {loading ? (
            <div className="empty">
              <span className="spinner" /> Loading…
            </div>
          ) : assets.length === 0 ? (
            <div className="empty">
              {inTrash ? "Trash is empty." : "No assets here yet. Upload something to get started."}
            </div>
          ) : view === "grid" ? (
            <div className="grid">
              {assets.map((a) => (
                <div key={a.id} style={{ position: "relative" }}>
                  <input
                    type="checkbox"
                    checked={selected.has(a.id)}
                    onClick={(e) => e.stopPropagation()}
                    onChange={() => toggle(a.id)}
                    style={{ position: "absolute", top: 10, left: 10, zIndex: 2, width: 18, height: 18, cursor: "pointer" }}
                  />
                  <AssetCard
                    id={a.id}
                    type={a.type}
                    title={a.title}
                    filename={a.filename}
                    thumbnailUri={a.thumbnail_uri}
                    signals={[a.status]}
                    onClick={() => router.push(`/asset/${a.id}`)}
                  />
                </div>
              ))}
            </div>
          ) : (
            <table className="tbl">
              <thead>
                <tr>
                  <th style={{ width: 36 }} />
                  <th>Title</th>
                  <th>Type</th>
                  <th>Status</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {assets.map((a) => (
                  <tr key={a.id} className="row" onClick={() => router.push(`/asset/${a.id}`)}>
                    <td onClick={(e) => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        checked={selected.has(a.id)}
                        onChange={() => toggle(a.id)}
                        style={{ width: 16, height: 16, cursor: "pointer" }}
                      />
                    </td>
                    <td>{a.title || a.filename}</td>
                    <td>
                      <span className="badge type">{a.type}</span>
                    </td>
                    <td>{a.status}</td>
                    <td className="muted">
                      {a.created_at ? new Date(a.created_at).toLocaleDateString() : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </AppShell>
  );
}
