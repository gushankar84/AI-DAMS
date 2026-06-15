"use client";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { uploadFile, getAsset } from "@/lib/api";
import AppShell from "@/components/AppShell";

type UploadStatus = "uploaded" | "processing" | "extracting" | "indexed" | "searchable" | "failed";
type UploadItem = { key: string; name: string; asset_id: string; status: string; error?: string };

const LIFECYCLE: Record<UploadStatus, string> = {
  uploaded: "Uploaded",
  processing: "AI Processing",
  extracting: "Metadata Extraction",
  indexed: "Indexed",
  searchable: "Searchable",
  failed: "Failed",
};

const SUPPORTED = ["PDF", "DOCX", "XLSX", "PPT", "TIFF", "JPG", "PNG", "WAV", "MP3", "MP4", "MOV", "MXF"];

function pillClass(status: string): string {
  if (status === "searchable") return "badge green";
  if (status === "failed") return "badge red";
  return "badge amber";
}

function lifecycleText(status: string): string {
  return LIFECYCLE[status as UploadStatus] || status;
}

export default function UploadPage() {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [items, setItems] = useState<UploadItem[]>([]);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  // Track in-flight poll timers so navigating away cancels them (else they keep hitting the
  // API and setState on an unmounted component for up to 3 minutes per upload).
  const pollTimers = useRef<ReturnType<typeof setTimeout>[]>([]);
  const mounted = useRef(true);
  useEffect(() => () => { mounted.current = false; pollTimers.current.forEach(clearTimeout); }, []);

  function setItemStatus(key: string, status: string) {
    setItems((prev) => prev.map((it) => (it.key === key ? { ...it, status } : it)));
  }

  function poll(key: string, assetId: string) {
    let tries = 0;
    const schedule = () => { pollTimers.current.push(setTimeout(tick, 3000)); };
    const tick = async () => {
      if (!mounted.current) return;            // bail if the page unmounted
      tries += 1;
      try {
        const detail = await getAsset(assetId);
        if (!mounted.current) return;
        setItemStatus(key, detail.status);
        if (detail.status === "searchable" || detail.status === "failed") return;
      } catch (err: any) {
        if (err?.message === "unauthorized") return;
        // transient error: keep polling until the cap
      }
      if (tries < 60 && mounted.current) schedule();
    };
    schedule();
  }

  async function handleFiles(files: FileList | File[]) {
    const list = Array.from(files);
    if (list.length === 0) return;
    setBusy(true);
    try {
      for (const file of list) {
        const key = `${file.name}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
        try {
          const res = await uploadFile(file);
          const newItem: UploadItem = { key, name: file.name, asset_id: res.asset_id, status: "uploaded" };
          setItems((prev) => [newItem, ...prev]);
          poll(key, res.asset_id);
        } catch (err: any) {
          if (err?.message === "unauthorized") return;
          // Keep WHY it failed so the user can fix it (size, format, network) — not just "Failed".
          setItems((prev) => [{ key, name: file.name, asset_id: "", status: "failed",
                                error: err?.message || "upload failed" }, ...prev]);
        }
      }
    } finally {
      setBusy(false);
    }
  }

  function onInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    if (e.target.files) handleFiles(e.target.files);
    e.target.value = "";
  }

  function onDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setOver(false);
    if (e.dataTransfer?.files) handleFiles(e.dataTransfer.files);
  }

  function onDragOver(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    if (!over) setOver(true);
  }

  function onDragEnter(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setOver(true);
  }

  function onDragLeave(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setOver(false);
  }

  return (
    <AppShell title="Upload" subtitle="Drag and drop documents, images, audio and video">
      <input
        ref={inputRef}
        type="file"
        multiple
        onChange={onInputChange}
        style={{ display: "none" }}
      />

      <div
        className={over ? "dropzone over" : "dropzone"}
        onClick={() => inputRef.current?.click()}
        onDrop={onDrop}
        onDragOver={onDragOver}
        onDragEnter={onDragEnter}
        onDragLeave={onDragLeave}
      >
        {busy ? <span className="spinner" /> : null}
        <div style={{ fontSize: 15, fontWeight: 600 }}>Drop files here or click to browse</div>
        <div className="muted" style={{ marginTop: 6 }}>
          Supported formats: {SUPPORTED.join(", ")}
        </div>
      </div>

      {items.length === 0 ? (
        <div className="empty">No uploads yet. Add files to start ingesting into the library.</div>
      ) : (
        <div className="panel" style={{ marginTop: 16 }}>
          {items.map((it) => (
            <div className="row" key={it.key}>
              <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {it.name}
              </span>
              <span className={pillClass(it.status)}>{lifecycleText(it.status)}</span>
              {it.status === "failed" && it.error ? (
                <span style={{ color: "var(--red, #e5484d)", fontSize: 12, maxWidth: 280,
                               overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                      title={it.error}>
                  {it.error}
                </span>
              ) : null}
              {it.status === "searchable" && it.asset_id ? (
                <button className="btn sm" onClick={() => router.push(`/asset/${it.asset_id}`)}>
                  Open
                </button>
              ) : null}
            </div>
          ))}
        </div>
      )}
    </AppShell>
  );
}
