"use client";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { listAssets, transitionWorkflow, TYPE_ICON, type Asset } from "@/lib/api";
import AppShell from "@/components/AppShell";

const STATES: { key: string; label: string }[] = [
  { key: "uploaded", label: "Uploaded" },
  { key: "under_review", label: "Under Review" },
  { key: "approved", label: "Approved" },
  { key: "published", label: "Published" },
  { key: "archived", label: "Archived" },
];

const LABELS: Record<string, string> = {
  uploaded: "Uploaded",
  under_review: "Under Review",
  approved: "Approved",
  published: "Published",
  archived: "Archived",
};

// Workflow badge color: amber = awaiting action, green = live/approved, red = retired.
const BADGE_TONE: Record<string, string> = {
  uploaded: "amber",
  under_review: "amber",
  approved: "green",
  published: "green",
  archived: "red",
};

// Contextual transitions available from each workflow state.
const TRANSITIONS: Record<string, { label: string; to: string; danger?: boolean }[]> = {
  uploaded: [{ label: "Submit for review", to: "under_review" }],
  under_review: [
    { label: "Approve", to: "approved" },
    { label: "Archive", to: "archived", danger: true },
  ],
  approved: [
    { label: "Publish", to: "published" },
    { label: "Archive", to: "archived", danger: true },
  ],
  published: [{ label: "Archive", to: "archived", danger: true }],
  archived: [{ label: "Restore to review", to: "under_review" }],
};

export default function WorkflowPage() {
  const router = useRouter();
  const [selected, setSelected] = useState("under_review");
  const [assets, setAssets] = useState<Asset[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [unauthorized, setUnauthorized] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async (state: string) => {
    setLoading(true);
    setError(null);
    try {
      const rows = await listAssets({ workflow: state, limit: 100 });
      setAssets(rows);
    } catch (err: any) {
      if (err?.message === "unauthorized") { setUnauthorized(true); return; }
      setError(err?.message || "Failed to load assets.");
      setAssets([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(selected); }, [load, selected]);

  async function act(id: string, to: string) {
    setBusy(id);
    setError(null);
    try {
      await transitionWorkflow(id, to);
      await load(selected);
    } catch (err: any) {
      if (err?.message === "unauthorized") { setUnauthorized(true); return; }
      setError(err?.message || "Transition failed.");
    } finally {
      setBusy(null);
    }
  }

  if (unauthorized) return null;

  return (
    <AppShell title="Workflows" subtitle="Review and approve assets through their lifecycle">
      <div className="filters">
        {STATES.map((s) => (
          <button
            key={s.key}
            className={`chip ${selected === s.key ? "active" : ""}`}
            onClick={() => setSelected(s.key)}
          >
            {s.label}
          </button>
        ))}
      </div>

      {error && <div className="toast err" style={{ marginBottom: 16 }}>{error}</div>}

      <div className="panel">
        {loading ? (
          <div className="empty"><span className="spinner" /></div>
        ) : assets.length === 0 ? (
          <div className="empty">No assets in “{LABELS[selected]}”.</div>
        ) : (
          <table className="tbl">
            <thead>
              <tr>
                <th>Asset</th>
                <th>Stage</th>
                <th style={{ textAlign: "right" }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {assets.map((a) => {
                const moves = TRANSITIONS[a.workflow] || [];
                return (
                  <tr key={a.id}>
                    <td>
                      <span
                        onClick={() => router.push(`/asset/${a.id}`)}
                        style={{ cursor: "pointer", display: "inline-flex", gap: 8, alignItems: "center" }}
                      >
                        <span>{TYPE_ICON[a.type] || "📁"}</span>
                        <span className="card-title" style={{ margin: 0 }}>{a.title || a.filename}</span>
                      </span>
                    </td>
                    <td>
                      <span className={`badge ${BADGE_TONE[a.workflow] || ""}`}>
                        {LABELS[a.workflow] || a.workflow}
                      </span>
                    </td>
                    <td style={{ textAlign: "right" }}>
                      <span style={{ display: "inline-flex", gap: 8, justifyContent: "flex-end", flexWrap: "wrap" }}>
                        {moves.length === 0 ? (
                          <span className="muted">—</span>
                        ) : (
                          moves.map((m) => (
                            <button
                              key={m.to}
                              className={`btn sm ${m.danger ? "danger" : ""}`}
                              disabled={busy === a.id}
                              onClick={() => act(a.id, m.to)}
                            >
                              {m.label}
                            </button>
                          ))
                        )}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </AppShell>
  );
}
