"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import type { DocText } from "@/lib/api";

/** In-app document reader with FIND-IN-DOCUMENT. Renders the extracted text (works for PDF and
 *  DOCX alike, independent of the browser's PDF plugin), highlights every match of the find term,
 *  and jumps between them. The visual original is one click away via "Open original". */
export default function DocReader({
  doc, originalUrl, initialQuery = "",
}: { doc: DocText; originalUrl: string | null; initialQuery?: string }) {
  const [q, setQ] = useState(initialQuery);
  const [cur, setCur] = useState(0);
  const scrollRef = useRef<HTMLDivElement>(null);

  const blocks = useMemo(
    () => (doc.pages && doc.pages.length
      ? doc.pages.map((p) => ({ label: `Page ${p.page}`, text: p.text }))
      : [{ label: "", text: doc.text || "" }]),
    [doc],
  );

  const needle = q.trim();
  // Split each block into [text, MATCH, text, ...]; number matches globally so we can jump.
  const { rendered, total } = useMemo(() => {
    if (!needle) return {
      rendered: blocks.map((b) => ({ label: b.label, parts: [{ t: b.text }] as { t: string; m?: number }[] })),
      total: 0,
    };
    const re = new RegExp(`(${needle.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "gi");
    let n = 0;
    const rendered = blocks.map((b) => {
      const parts: { t: string; m?: number }[] = [];
      b.text.split(re).forEach((s, i) => {
        if (i % 2 === 1) parts.push({ t: s, m: n++ });
        else if (s) parts.push({ t: s });
      });
      return { label: b.label, parts };
    });
    return { rendered, total: n };
  }, [needle, blocks]);

  // Keep the active match in view.
  useEffect(() => {
    if (!total) return;
    const el = scrollRef.current?.querySelector(`[data-m="${cur}"]`) as HTMLElement | null;
    el?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [cur, total]);
  useEffect(() => { setCur(0); }, [needle]);

  const go = (d: number) => total && setCur((c) => (c + d + total) % total);

  return (
    <div style={{ width: "100%" }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8, flexWrap: "wrap" }}>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") go(e.shiftKey ? -1 : 1); }}
          placeholder="Find in document…"
          style={{ flex: "1 1 220px", minWidth: 160, padding: "8px 12px", borderRadius: 8,
                   border: "1px solid var(--border,#333)", background: "var(--panel,#111)", color: "inherit" }}
        />
        {needle !== "" && (
          <span className="muted" style={{ fontSize: 13, minWidth: 70, textAlign: "center" }}>
            {total ? `${cur + 1} of ${total}` : "0 results"}
          </span>
        )}
        <button className="btn" disabled={!total} onClick={() => go(-1)} title="Previous (Shift+Enter)">↑</button>
        <button className="btn" disabled={!total} onClick={() => go(1)} title="Next (Enter)">↓</button>
        {originalUrl && (
          <button className="btn" onClick={() => window.open(originalUrl, "_blank", "noreferrer")}
                  title="Open the original file in a new tab">Open original ↗</button>
        )}
      </div>
      <div ref={scrollRef}
           style={{ height: "62vh", overflowY: "auto", padding: "14px 16px", borderRadius: 8,
                    border: "1px solid var(--border,#333)", background: "var(--panel,#0d0d0d)",
                    whiteSpace: "pre-wrap", wordBreak: "break-word", lineHeight: 1.55, fontSize: 14 }}>
        {(doc.text || (doc.pages && doc.pages.length)) ? rendered.map((b, bi) => (
          <div key={bi} style={{ marginBottom: b.label ? 18 : 0 }}>
            {b.label && (
              <div className="muted" style={{ fontSize: 11, textTransform: "uppercase",
                   letterSpacing: 0.5, margin: "10px 0 4px", opacity: 0.7 }}>{b.label}</div>
            )}
            {b.parts.map((p, i) =>
              p.m === undefined ? <span key={i}>{p.t}</span> : (
                <mark key={i} data-m={p.m}
                      style={{ background: p.m === cur ? "#ffb300" : "#5a4a00",
                               color: p.m === cur ? "#000" : "inherit", borderRadius: 2, padding: "0 1px" }}>
                  {p.t}
                </mark>
              ))}
          </div>
        )) : <span className="muted">No extracted text for this document.</span>}
      </div>
    </div>
  );
}
