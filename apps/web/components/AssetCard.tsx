"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { mediaUrl, TYPE_ICON } from "@/lib/api";

/** Search highlights arrive as text wrapped in <em> by the backend. That text is OCR/
 *  caption/transcript content — user-influenced — so escape ALL html and then re-allow ONLY
 *  the <em> highlight. Neutralizes stored XSS (e.g. an <img onerror> baked into a caption)
 *  while keeping the bolded match. */
function safeHighlight(s: string): string {
  const esc = s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return esc.replace(/&lt;em&gt;/g, "<em>").replace(/&lt;\/em&gt;/g, "</em>");
}

/** Reusable asset/result card with lazy thumbnail. Works for Asset and SearchHit shapes. */
export default function AssetCard({ id, type, title, filename, thumbnailUri, snippet, caption, signals, timeline, onClick }: {
  id: string; type: string; title?: string | null; filename: string;
  thumbnailUri?: string | null; snippet?: string | null; caption?: string | null; signals?: string[];
  timeline?: { smpte: string | null; frame_index: number | null; snippet: string | null; label: string | null; kind?: string; page?: number | null }[];
  onClick?: () => void;
}) {
  const router = useRouter();
  const [thumb, setThumb] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    if (thumbnailUri) mediaUrl(id, "thumbnail").then((u) => alive && setThumb(u));
    return () => { alive = false; };
  }, [id, thumbnailUri]);

  // Opening the card lands on the MATCHED MOMENT, not 0:00/page 1: use the first timeline hit
  // that carries a frame or page (the specific chips below still let you pick another).
  const jump = (timeline || []).find((t) => t.page != null || t.frame_index != null);
  const cardDest = jump
    ? (jump.page != null ? `/asset/${id}?page=${jump.page}` : `/asset/${id}?f=${jump.frame_index}`)
    : `/asset/${id}`;

  return (
    <div className="card" onClick={onClick || (() => router.push(cardDest))}>
      <div className="thumb">
        {thumb ? <img src={thumb} alt={title || filename} /> : <span className="ph">{TYPE_ICON[type] || "📁"}</span>}
      </div>
      <div className="card-body">
        <div className="card-title">{title || filename}</div>
        {snippet ? (
          <div className="snippet" dangerouslySetInnerHTML={{ __html: safeHighlight(snippet) }} />
        ) : caption ? (
          // No text match (e.g. a visual/image hit) — show what the system sees so the
          // card isn't bare and the user understands why it matched.
          <div className="snippet" style={{ color: "var(--muted-2)" }}>{caption}</div>
        ) : null}
        <div className="badges">
          <span className="badge type">{type}</span>
          {(signals || []).map((s) => <span key={s} className="badge">{s}</span>)}
        </div>
        {timeline && timeline.length > 0 && (
          <div style={{ marginTop: 8, borderTop: "1px solid var(--border)", paddingTop: 8 }}>
            {timeline.slice(0, 3).map((t, i) => {
              // Clickable: jump STRAIGHT to the matched moment — ?f= seeks the player there;
              // for documents ?page= opens the PDF at the matched PAGE. Never land at 0:00/p.1.
              const dest = t.page != null ? `/asset/${id}?page=${t.page}`
                : t.frame_index != null ? `/asset/${id}?f=${t.frame_index}` : null;
              const stamp = t.page != null ? `p.${t.page}` : (t.smpte || `#${t.frame_index}`);
              return (
                <div key={i}
                  style={{ display: "flex", gap: 8, fontSize: 11, color: "var(--muted)", padding: "2px 0",
                           cursor: dest ? "pointer" : "inherit" }}
                  title={dest ? (t.page != null ? "Open at this page" : "Play from this moment") : undefined}
                  onClick={dest ? (e) => { e.stopPropagation(); router.push(dest); } : undefined}>
                  <span style={{ color: "var(--accent)", whiteSpace: "nowrap",
                                 textDecoration: dest ? "underline dotted" : "none" }}>
                    {stamp}{dest ? " ▶" : ""}
                  </span>
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                    dangerouslySetInnerHTML={{ __html: safeHighlight(t.snippet || t.label || t.kind || "") }} />
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
