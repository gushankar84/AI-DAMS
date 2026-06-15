"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  getStats, getActivity, getTrending, getMostViewed,
  fmtBytes, TYPE_ICON,
  Stats, Activity,
} from "@/lib/api";
import AppShell from "@/components/AppShell";
import AssetCard from "@/components/AssetCard";

type Trending = { query: string; count: number };

function relTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (isNaN(then)) return "";
  const diff = Math.max(0, Date.now() - then);
  const s = Math.floor(diff / 1000);
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 30) return `${d}d ago`;
  const mo = Math.floor(d / 30);
  if (mo < 12) return `${mo}mo ago`;
  return `${Math.floor(mo / 12)}y ago`;
}

function queueBadge(key: string): string {
  const k = key.toLowerCase();
  if (k.includes("searchable") || k.includes("ready") || k.includes("done") || k.includes("complete")) return "badge green";
  if (k.includes("fail") || k.includes("error")) return "badge red";
  return "badge amber";
}

export default function DashboardPage() {
  const router = useRouter();
  const [loading, setLoading] = useState(true);
  const [stats, setStats] = useState<Stats | null>(null);
  const [activity, setActivity] = useState<Activity[]>([]);
  const [trending, setTrending] = useState<Trending[]>([]);
  const [viewed, setViewed] = useState<any[]>([]);

  useEffect(() => {
    let alive = true;
    (async () => {
      const guard = async <T,>(p: Promise<T>, fallback: T): Promise<T> => {
        try { return await p; }
        catch (err: any) {
          if (err && err.message === "unauthorized") throw err;
          return fallback;
        }
      };
      try {
        const [s, a, t, v] = await Promise.all([
          guard(getStats(), null as Stats | null),
          guard(getActivity(12), [] as Activity[]),
          guard(getTrending(8), [] as Trending[]),
          guard(getMostViewed(8), [] as any[]),
        ]);
        if (!alive) return;
        setStats(s);
        setActivity(a);
        setTrending(t);
        setViewed(v);
      } catch (err: any) {
        if (err && err.message === "unauthorized") return; // AppShell handles the redirect
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, []);

  const tiles: { num: string | number; icon: string; label: string }[] = stats ? [
    { num: stats.total_assets, icon: "📦", label: "Total assets" },
    { num: stats.by_type.document || 0, icon: TYPE_ICON.document, label: "Documents" },
    { num: stats.by_type.image || 0, icon: TYPE_ICON.image, label: "Images" },
    { num: stats.by_type.video || 0, icon: TYPE_ICON.video, label: "Videos" },
    { num: stats.by_type.audio || 0, icon: TYPE_ICON.audio, label: "Audio" },
    { num: fmtBytes(stats.storage_bytes), icon: "💾", label: "Storage" },
    { num: stats.persons, icon: "🧑", label: "People" },
    { num: stats.collections, icon: "🗂️", label: "Collections" },
  ] : [];

  const queueEntries = stats ? Object.entries(stats.queue) : [];

  return (
    <AppShell title="Dashboard" subtitle="Your repository at a glance">
      {loading ? (
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span className="spinner" /> <span className="muted">Loading…</span>
        </div>
      ) : !stats ? (
        <div className="empty">Could not load dashboard stats.</div>
      ) : (
        <>
          <div className="tiles">
            {tiles.map((t) => (
              <div className="tile" key={t.label}>
                <div className="n">{t.num}</div>
                <div className="l"><span>{t.icon}</span>{t.label}</div>
              </div>
            ))}
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: 18, alignItems: "start" }}>
            {/* LEFT column */}
            <div style={{ display: "grid", gap: 18 }}>
              <div className="panel">
                <h3>AI Processing Queue</h3>
                {queueEntries.length === 0 ? (
                  <div className="muted" style={{ fontSize: 13 }}>Queue is clear — nothing processing.</div>
                ) : (
                  queueEntries.map(([key, count]) => (
                    <div className="row" key={key}>
                      <span style={{ flex: 1, textTransform: "capitalize" }}>{key.replace(/_/g, " ")}</span>
                      <span className="muted">{count}</span>
                      <span className={queueBadge(key)}>{key.replace(/_/g, " ")}</span>
                    </div>
                  ))
                )}
              </div>

              <div className="panel">
                <h3>Most viewed</h3>
                {viewed.length === 0 ? (
                  <div className="muted" style={{ fontSize: 13 }}>No views recorded yet.</div>
                ) : (
                  <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))" }}>
                    {viewed.map((a) => (
                      <AssetCard
                        key={a.asset_id || a.id}
                        id={a.asset_id || a.id}
                        type={a.type}
                        title={a.title}
                        filename={a.filename}
                        thumbnailUri={a.thumbnail_uri}
                      />
                    ))}
                  </div>
                )}
              </div>
            </div>

            {/* RIGHT column */}
            <div style={{ display: "grid", gap: 18 }}>
              <div className="panel">
                <h3>Recent activity</h3>
                {activity.length === 0 ? (
                  <div className="muted" style={{ fontSize: 13 }}>No recent activity.</div>
                ) : (
                  activity.map((act, i) => {
                    const q = act.detail && typeof act.detail.q === "string" ? act.detail.q : null;
                    return (
                      <div className="row" key={i}>
                        <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis" }}>
                          <span className="muted">{act.actor}</span>{" "}
                          <strong>{act.action.replace(/_/g, " ")}</strong>
                          {q && <span className="muted"> “{q}”</span>}
                        </span>
                        <span className="muted" style={{ whiteSpace: "nowrap", fontSize: 12 }}>{relTime(act.created_at)}</span>
                      </div>
                    );
                  })
                )}
              </div>

              <div className="panel">
                <h3>Trending searches</h3>
                {trending.length === 0 ? (
                  <div className="muted" style={{ fontSize: 13 }}>No trending searches.</div>
                ) : (
                  trending.map((t, i) => (
                    <div
                      className="row"
                      key={i}
                      style={{ cursor: "pointer" }}
                      onClick={() => router.push("/search?q=" + encodeURIComponent(t.query))}
                    >
                      <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.query}</span>
                      <span className="badge">{t.count}</span>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        </>
      )}
    </AppShell>
  );
}
