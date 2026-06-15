"use client";
import { useEffect, useState } from "react";
import {
  getStats,
  getTrending,
  getActivity,
  fmtBytes,
  type Stats,
  type Activity,
} from "@/lib/api";
import AppShell from "@/components/AppShell";

type Trend = { query: string; count: number };

function BarChart({ data }: { data: Record<string, number> }) {
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1]);
  if (entries.length === 0) {
    return <div className="empty">No data yet.</div>;
  }
  const max = Math.max(...entries.map(([, v]) => v), 1);
  return (
    <div>
      {entries.map(([label, count]) => (
        <div key={label} style={{ margin: "12px 0" }}>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              fontSize: 13,
              marginBottom: 6,
            }}
          >
            <span style={{ textTransform: "capitalize" }}>{label.replace(/_/g, " ")}</span>
            <span className="muted">{count}</span>
          </div>
          <div
            style={{
              width: "100%",
              height: 10,
              background: "var(--panel-2)",
              borderRadius: 6,
              overflow: "hidden",
            }}
          >
            <div
              style={{
                width: `${(count / max) * 100}%`,
                height: 10,
                background: "var(--accent)",
                borderRadius: 6,
              }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}

function fmtTime(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

export default function ReportsPage() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [trending, setTrending] = useState<Trend[]>([]);
  const [activity, setActivity] = useState<Activity[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [unauthorized, setUnauthorized] = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [s, t, a] = await Promise.all([
          getStats(),
          getTrending(15),
          getActivity(40),
        ]);
        if (!alive) return;
        setStats(s);
        setTrending(t);
        setActivity(a);
      } catch (err: any) {
        if (!alive) return;
        if (err?.message === "unauthorized") {
          setUnauthorized(true);
          return;
        }
        setError(err?.message || "Failed to load reports.");
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  if (unauthorized) return null;

  return (
    <AppShell title="Reports" subtitle="Usage and content analytics">
      {loading ? (
        <div className="empty">
          <span className="spinner" /> Loading…
        </div>
      ) : error ? (
        <div className="err">{error}</div>
      ) : (
        <div style={{ display: "grid", gap: 16 }}>
          <div className="panel">
            <h3>Assets by type</h3>
            <BarChart data={stats?.by_type || {}} />
          </div>

          <div className="panel">
            <h3>Workflow distribution</h3>
            <BarChart data={stats?.by_workflow || {}} />
          </div>

          <div className="panel">
            <h3>Storage &amp; totals</h3>
            <div className="kv">Total assets <b>{stats?.total_assets ?? 0}</b></div>
            <div className="kv">Storage <b>{fmtBytes(stats?.storage_bytes)}</b></div>
            <div className="kv">People <b>{stats?.persons ?? 0}</b></div>
            <div className="kv">Collections <b>{stats?.collections ?? 0}</b></div>
            <div className="kv">Trash <b>{stats?.trash ?? 0}</b></div>
          </div>

          <div className="panel">
            <h3>Top searches</h3>
            {trending.length === 0 ? (
              <div className="empty">No searches recorded yet.</div>
            ) : (
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Query</th>
                    <th style={{ textAlign: "right" }}>Count</th>
                  </tr>
                </thead>
                <tbody>
                  {trending.map((t, i) => (
                    <tr key={`${t.query}-${i}`}>
                      <td>{t.query}</td>
                      <td style={{ textAlign: "right" }}>{t.count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          <div className="panel">
            <h3>Recent activity</h3>
            {activity.length === 0 ? (
              <div className="empty">No recent activity.</div>
            ) : (
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Actor</th>
                    <th>Action</th>
                    <th>Target</th>
                  </tr>
                </thead>
                <tbody>
                  {activity.map((a, i) => {
                    const target =
                      (a.detail && (a.detail.q || a.detail.query)) ||
                      [a.target_type, a.target_id].filter(Boolean).join(" · ") ||
                      "—";
                    return (
                      <tr key={`${a.created_at}-${i}`}>
                        <td className="muted">{fmtTime(a.created_at)}</td>
                        <td>{a.actor}</td>
                        <td>{a.action}</td>
                        <td>{target}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}
    </AppShell>
  );
}
