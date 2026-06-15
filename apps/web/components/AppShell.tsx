"use client";
import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { clearToken, getStats, getToken, me, type Stats, type User } from "@/lib/api";

const NAV = [
  { href: "/", label: "Dashboard", ico: "▣" },
  { href: "/search", label: "Search", ico: "🔍" },
  { href: "/explorer", label: "Assets", ico: "🗂" },
  { href: "/collections", label: "Collections", ico: "📚" },
  { href: "/upload", label: "Upload", ico: "⤴" },
  { href: "/workflow", label: "Workflows", ico: "✔" },
  { href: "/distribution", label: "Distribution", ico: "🔗" },
  { href: "/reports", label: "Reports", ico: "📊" },
  { href: "/admin", label: "Admin", ico: "⚙" },
];

export default function AppShell({ children, title, subtitle, search = true }:
  { children: React.ReactNode; title?: string; subtitle?: string; search?: boolean }) {
  const router = useRouter();
  const path = usePathname();
  const [user, setUser] = useState<User | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [q, setQ] = useState("");

  useEffect(() => {
    let alive = true;
    if (!getToken()) { router.push("/login"); return; }
    me().then((u) => alive && setUser(u)).catch(() => router.push("/login"));
    getStats().then((s) => alive && setStats(s)).catch(() => {});
    return () => { alive = false; };
  }, [router]);

  const counts: Record<string, number | undefined> = {
    "/explorer": stats?.total_assets,
    "/collections": stats?.collections,
    "/workflow": stats?.by_workflow?.under_review,
  };

  function submitSearch(e: React.FormEvent) {
    e.preventDefault();
    if (q.trim()) router.push(`/search?q=${encodeURIComponent(q)}`);
  }

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand" onClick={() => router.push("/")} style={{ cursor: "pointer" }}>DAM<span>·</span>AI</div>
        {NAV.map((n) => {
          const active = n.href === "/" ? path === "/" : path.startsWith(n.href);
          return (
            <div key={n.href} className={`navlink ${active ? "active" : ""}`} onClick={() => router.push(n.href)}>
              <span className="ico">{n.ico}</span>{n.label}
              {counts[n.href] != null && <span className="count">{counts[n.href]}</span>}
            </div>
          );
        })}
        <div className="sidebar-foot">
          {user && <div>{user.display_name}<br /><span style={{ color: "var(--muted-2)" }}>{user.role}</span></div>}
          <div className="navlink" style={{ marginTop: 8, padding: "6px 0" }} onClick={() => { clearToken(); router.push("/login"); }}>↩ Sign out</div>
        </div>
      </aside>

      <div className="main">
        <div className="topbar">
          {search ? (
            <form className="searchbar" onSubmit={submitSearch}>
              <span style={{ color: "var(--muted-2)" }}>🔍</span>
              <input value={q} onChange={(e) => setQ(e.target.value)}
                placeholder="Search everything — a spoken phrase, a person, an object, a document…" />
            </form>
          ) : <div style={{ flex: 1 }} />}
          <button className="btn ghost sm" onClick={() => router.push("/upload")}>⤴ Upload</button>
        </div>
        <div className="content">
          {title && <h1 className="page-title">{title}</h1>}
          {subtitle && <p className="page-sub">{subtitle}</p>}
          {children}
        </div>
      </div>
    </div>
  );
}
