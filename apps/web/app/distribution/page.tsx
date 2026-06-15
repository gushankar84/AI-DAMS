"use client";
import { useEffect, useState } from "react";
import { createShare, listShares, revokeShare, type Share } from "@/lib/api";
import AppShell from "@/components/AppShell";

type ScopeType = "asset" | "collection";
type Permission = "view" | "download" | "edit" | "admin";

const PERMISSION_BADGE: Record<string, string> = {
  view: "",
  download: "green",
  edit: "amber",
  admin: "red",
};

export default function DistributionPage() {
  const [shares, setShares] = useState<Share[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [unauthorized, setUnauthorized] = useState(false);

  // Create-share form state.
  const [scopeType, setScopeType] = useState<ScopeType>("asset");
  const [scopeId, setScopeId] = useState("");
  const [permission, setPermission] = useState<Permission>("view");
  const [expiry, setExpiry] = useState("");
  const [watermark, setWatermark] = useState(false);
  const [creating, setCreating] = useState(false);

  const [toast, setToast] = useState<string | null>(null);

  function flash(msg: string) {
    setToast(msg);
    setTimeout(() => setToast(null), 2600);
  }

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const data = await listShares();
      setShares(data);
    } catch (err: any) {
      if (err.message === "unauthorized") { setUnauthorized(true); return; }
      setShares([]);
      setError("Could not load shares. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  async function onCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!scopeId.trim() || creating) return;
    setCreating(true);
    setError(null);
    try {
      await createShare({
        scope_type: scopeType,
        scope_id: scopeId.trim(),
        permission,
        expiry: expiry ? new Date(expiry).toISOString() : null,
        watermark,
      });
      setScopeId("");
      setExpiry("");
      setWatermark(false);
      flash("Share link created");
      await load();
    } catch (err: any) {
      if (err.message === "unauthorized") { setUnauthorized(true); return; }
      setError("Could not create share link. Check the scope ID and try again.");
    } finally {
      setCreating(false);
    }
  }

  async function onCopy(share: Share) {
    try {
      await navigator.clipboard.writeText(share.url);
      flash("Link copied to clipboard");
    } catch {
      flash("Could not copy link");
    }
  }

  async function onRevoke(id: string) {
    if (!confirm("Revoke this share link? Anyone holding the link will immediately lose access.")) return;
    try {
      await revokeShare(id);
      flash("Share revoked");
      await load();
    } catch (err: any) {
      if (err.message === "unauthorized") { setUnauthorized(true); return; }
      setError("Could not revoke share. Please try again.");
    }
  }

  if (unauthorized) return null;

  return (
    <AppShell title="Distribution" subtitle="Share assets and collections with controlled, expiring links">
      <div className="panel" style={{ marginBottom: 22 }}>
        <h3>Create share</h3>
        <form onSubmit={onCreate}>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 14 }}>
            <div className="field-group" style={{ flex: "1 1 200px", marginBottom: 0 }}>
              <label className="lbl">Scope</label>
              <select
                className="field"
                value={scopeType}
                onChange={(e) => setScopeType(e.target.value as ScopeType)}
              >
                <option value="asset">Asset</option>
                <option value="collection">Collection</option>
              </select>
            </div>

            <div className="field-group" style={{ flex: "2 1 280px", marginBottom: 0 }}>
              <label className="lbl">Scope ID</label>
              <input
                className="field"
                value={scopeId}
                onChange={(e) => setScopeId(e.target.value)}
                placeholder="Paste an asset or collection ID"
              />
              <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
                usually created from an asset&rsquo;s Share button
              </div>
            </div>

            <div className="field-group" style={{ flex: "1 1 160px", marginBottom: 0 }}>
              <label className="lbl">Permission</label>
              <select
                className="field"
                value={permission}
                onChange={(e) => setPermission(e.target.value as Permission)}
              >
                <option value="view">View</option>
                <option value="download">Download</option>
                <option value="edit">Edit</option>
                <option value="admin">Admin</option>
              </select>
            </div>

            <div className="field-group" style={{ flex: "1 1 220px", marginBottom: 0 }}>
              <label className="lbl">Expiry (optional)</label>
              <input
                className="field"
                type="datetime-local"
                value={expiry}
                onChange={(e) => setExpiry(e.target.value)}
              />
            </div>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 16, marginTop: 16, flexWrap: "wrap" }}>
            <label className="lbl" style={{ display: "flex", alignItems: "center", gap: 8, margin: 0, cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={watermark}
                onChange={(e) => setWatermark(e.target.checked)}
              />
              Apply watermark
            </label>
            <button className="btn" type="submit" disabled={creating || !scopeId.trim()}>
              {creating ? <span className="spinner" /> : "Create link"}
            </button>
          </div>
        </form>
        {error && <div className="err" style={{ marginTop: 14, marginBottom: 0 }}>{error}</div>}
      </div>

      <div className="panel">
        <h3>Active shares</h3>
        {loading ? (
          <div className="empty"><span className="spinner" /> Loading&hellip;</div>
        ) : !shares || shares.length === 0 ? (
          <div className="empty">No share links yet. Create one above to distribute an asset or collection.</div>
        ) : (
          <table className="tbl">
            <thead>
              <tr>
                <th>Scope</th>
                <th>Permission</th>
                <th>Expiry</th>
                <th>Watermark</th>
                <th>Link</th>
                <th style={{ textAlign: "right" }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {shares.map((s) => (
                <tr key={s.id}>
                  <td>
                    <span className="badge type">{s.scope_type}</span>{" "}
                    <span className="muted" style={{ fontVariantNumeric: "tabular-nums" }}>
                      {s.scope_id.slice(0, 8)}
                    </span>
                  </td>
                  <td>
                    <span className={`badge ${PERMISSION_BADGE[s.permission] || ""}`}>{s.permission}</span>
                  </td>
                  <td>{s.expiry ? new Date(s.expiry).toLocaleString() : "—"}</td>
                  <td>{s.watermark ? "✓" : "—"}</td>
                  <td>
                    <button className="btn ghost sm" type="button" onClick={() => onCopy(s)}>Copy</button>
                  </td>
                  <td style={{ textAlign: "right" }}>
                    <button className="btn danger sm" type="button" onClick={() => onRevoke(s.id)}>Revoke</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {toast && <div className="toast">{toast}</div>}
    </AppShell>
  );
}
