// ──────────────────────────────────────────────────────────────────────────
// DAM Platform — API client (the single contract every screen uses).
// Token in localStorage; Bearer auth. All calls return parsed JSON or throw.
// ──────────────────────────────────────────────────────────────────────────
const BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("dam_token");
}
export function setToken(t: string) { localStorage.setItem("dam_token", t); }
export function clearToken() { localStorage.removeItem("dam_token"); }

function authHeaders(): Record<string, string> {
  const t = getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

async function req(path: string, opts: RequestInit = {}): Promise<any> {
  const res = await fetch(`${BASE}${path}`, {
    ...opts,
    headers: { ...(opts.body && !(opts.body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
               ...authHeaders(), ...(opts.headers || {}) },
  });
  if (res.status === 401) { if (typeof window !== "undefined" && !path.includes("/auth/login")) clearToken(); throw new Error("unauthorized"); }
  if (!res.ok) throw new Error((await res.text().catch(() => "")) || `HTTP ${res.status}`);
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : res.text();
}

// ─── Types ──────────────────────────────────────────────────────────────────
export type AssetType = "document" | "image" | "audio" | "video";
export type Asset = {
  id: string; type: AssetType; status: string; error_detail?: string | null;
  title: string | null; description: string | null;
  filename: string; mime_type: string | null; size_bytes: number | null; storage_uri: string;
  thumbnail_uri: string | null; tags: string[]; department: string | null; project: string | null;
  rights: string | null; copyright: string | null; expiry_date: string | null; language: string | null;
  workflow: string; created_at: string;
};
export type Marker = { id: string; kind: string; frame_index: number | null; end_frame: number | null;
  start_seconds: number | null; smpte: string | null; label: string | null; person_id: string | null;
  confidence: number | null; payload: Record<string, any>; };
export type Transcript = { id: string; start_frame: number; end_frame: number; start_seconds: number | null;
  speaker: string | null; text: string; };
export type AssetDetail = Asset & { markers: Marker[]; transcript: Transcript[] };
export type TimelineHit = { frame_index: number | null; smpte: string | null; kind: string; label: string | null; snippet: string | null; page?: number | null };
export type SearchHit = { asset_id: string; type: AssetType; title: string | null; filename: string;
  thumbnail_uri: string | null; score: number; matched_signals: string[]; snippet: string | null;
  caption?: string | null; timeline: TimelineHit[]; };
export type QueryConcept = { term: string; role: string; idf: number; df: number };
export type SearchResponse = { query: string; total: number; took_ms: number; hits: SearchHit[]; concepts?: QueryConcept[] | null; intent?: string | null; degraded?: boolean };
export type Stats = { total_assets: number; storage_bytes: number; by_type: Record<string, number>;
  by_status: Record<string, number>; by_workflow: Record<string, number>; queue: Record<string, number>;
  persons: number; collections: number; trash: number; };
export type Activity = { action: string; target_type: string | null; target_id: string | null;
  detail: Record<string, any>; created_at: string; actor: string; };
export type Collection = { id: string; name: string; description: string | null; item_count: number };
export type Person = { id: string; display_name: string | null; consent_status: string; face_count: number; thumb_url?: string | null };
export type MergeSuggestion = { person_id: string; similar_to: string; score: number; suggested_name: string | null };
export const mergePersons = (targetId: string, sourceIds: string[]) =>
  req(`/api/persons/${targetId}/merge`, { method: "POST", body: JSON.stringify({ source_ids: sourceIds }) });
export const splitPerson = (id: string): Promise<{ new_person: string; moved: number; remaining: number }> =>
  req(`/api/persons/${id}/split`, { method: "POST" });
export const personSuggestions = (): Promise<MergeSuggestion[]> => req("/api/persons/suggestions");
export const personAssets = (id: string): Promise<{ id: string; filename: string; type: AssetType; face_url: string | null }[]> =>
  req(`/api/persons/${id}/assets`);
export type Share = { id: string; token: string; url: string; scope_type: string; scope_id: string;
  permission: string; expiry: string | null; watermark: boolean; };
export type User = { id: string; email: string; display_name: string; role: string; is_active: boolean };

// ─── Auth ─────────────────────────────────────────────────────────────────
export async function login(email: string, password: string): Promise<string> {
  const body = new URLSearchParams({ username: email, password });
  const res = await fetch(`${BASE}/api/auth/login`, { method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" }, body });
  if (!res.ok) throw new Error("Login failed");
  const data = await res.json();
  setToken(data.access_token);
  return data.access_token;
}
export const me = (): Promise<User> => req("/api/auth/me");

// ─── Assets ───────────────────────────────────────────────────────────────
export function listAssets(params: Record<string, any> = {}): Promise<Asset[]> {
  const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v != null && v !== "").map(([k, v]) => [k, String(v)]));
  return req(`/api/assets?${q}`);
}
export const getAsset = (id: string): Promise<AssetDetail> => req(`/api/assets/${id}`);
export const updateAsset = (id: string, patch: Record<string, any>) => req(`/api/assets/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
export const deleteAsset = (id: string) => req(`/api/assets/${id}`, { method: "DELETE" });
export const restoreAsset = (id: string) => req(`/api/assets/${id}/restore`, { method: "POST" });
export const reprocessAsset = (id: string) => req(`/api/assets/${id}/reprocess`, { method: "POST" });
export async function mediaUrl(id: string, variant = "original"): Promise<string | null> {
  try { return (await req(`/api/assets/${id}/media?variant=${variant}`)).url; } catch { return null; }
}
export type DocText = { text: string; pages: { page: number; text: string }[] | null; filename: string; type: string };
export async function assetText(id: string): Promise<DocText | null> {
  try { return await req(`/api/assets/${id}/text`); } catch { return null; }
}
export async function uploadFile(file: File, meta: { title?: string; department?: string; project?: string } = {}) {
  const fd = new FormData();
  fd.append("file", file);
  for (const k of ["title", "department", "project"] as const) if (meta[k]) fd.append(k, meta[k]!);
  return req("/api/assets", { method: "POST", body: fd });
}

// ─── Search ───────────────────────────────────────────────────────────────
export const search = (payload: Record<string, unknown>): Promise<SearchResponse> =>
  req("/api/search", { method: "POST", body: JSON.stringify(payload) });
export type Suggestion = { text: string; type: string };
export const searchSuggest = (q: string): Promise<{ suggestions: Suggestion[] }> =>
  req(`/api/search/suggest?q=${encodeURIComponent(q)}`);
export type FacetValue = { value: string; count: number };
export const searchFacets = (): Promise<Record<string, FacetValue[]>> => req("/api/search/facets");
export async function faceSearch(file: File): Promise<SearchResponse> {
  const fd = new FormData(); fd.append("file", file);
  return req("/api/search/face", { method: "POST", body: fd });
}

// ─── Workflow ───────────────────────────────────────────────────────────────
export const transitionWorkflow = (id: string, state: string, note?: string) =>
  req(`/api/assets/${id}/workflow`, { method: "POST", body: JSON.stringify({ state, note }) });
export const workflowHistory = (id: string) => req(`/api/assets/${id}/workflow`);

// ─── Collections ────────────────────────────────────────────────────────────
export const listCollections = (): Promise<Collection[]> => req("/api/collections");
export const createCollection = (name: string, description?: string) =>
  req("/api/collections", { method: "POST", body: JSON.stringify({ name, description }) });
export const getCollection = (id: string): Promise<{ id: string; name: string; description: string | null; assets: Asset[] }> => req(`/api/collections/${id}`);
export const addToCollection = (cid: string, aid: string) => req(`/api/collections/${cid}/items/${aid}`, { method: "POST" });
export const removeFromCollection = (cid: string, aid: string) => req(`/api/collections/${cid}/items/${aid}`, { method: "DELETE" });

// ─── Distribution ─────────────────────────────────────────────────────────
export const createShare = (body: Record<string, any>): Promise<Share> => req("/api/shares", { method: "POST", body: JSON.stringify(body) });
export const listShares = (): Promise<Share[]> => req("/api/shares");
export const revokeShare = (id: string) => req(`/api/shares/${id}`, { method: "DELETE" });

// ─── Persons / consent ───────────────────────────────────────────────────
export const listPersons = (): Promise<Person[]> => req("/api/persons");
export const updatePerson = (id: string, patch: Record<string, any>) => req(`/api/persons/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
export const generatePersonFace = (id: string): Promise<{ thumb_url: string }> => req(`/api/persons/${id}/face`, { method: "POST" });

// ─── Stats / dashboard ──────────────────────────────────────────────────
export const getStats = (): Promise<Stats> => req("/api/stats");
export const getActivity = (limit = 25): Promise<Activity[]> => req(`/api/stats/activity?limit=${limit}`);
export const getTrending = (limit = 10): Promise<{ query: string; count: number }[]> => req(`/api/stats/trending?limit=${limit}`);
export const getMostViewed = (limit = 8): Promise<any[]> => req(`/api/stats/most-viewed?limit=${limit}`);

// ─── Admin ────────────────────────────────────────────────────────────────
export const listUsers = (): Promise<User[]> => req("/api/admin/users");
export const createUser = (body: Record<string, any>): Promise<User> => req("/api/admin/users", { method: "POST", body: JSON.stringify(body) });
export const adminModels = () => req("/api/admin/models");
export const adminQueue = () => req("/api/admin/queue");
export const reprocessFailed = () => req("/api/admin/reprocess-failed", { method: "POST" });

// ─── Helpers ──────────────────────────────────────────────────────────────
export function fmtBytes(n: number | null | undefined): string {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0; let v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v < 10 && i > 0 ? 1 : 0)} ${u[i]}`;
}
export function fmtClock(s: number | null | undefined): string {
  if (s == null) return "";
  const t = Math.max(0, Math.floor(s));
  return `${String(Math.floor(t / 3600)).padStart(2, "0")}:${String(Math.floor((t % 3600) / 60)).padStart(2, "0")}:${String(t % 60).padStart(2, "0")}`;
}
export const TYPE_ICON: Record<string, string> = { document: "📄", image: "🖼️", video: "🎬", audio: "🎧" };
export { BASE };
