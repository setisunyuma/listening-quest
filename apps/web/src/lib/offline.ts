// Device-side storage, separated per user:
//  - IndexedDB "lq-<userId>": outbox (answers waiting to be graded) and packs (reserved slots without answers)
//  - Cache Storage "lq-audio-<userId>": explicitly downloaded audio, verified by size and SHA-256
// The Service Worker never touches these, so an app update cannot drop unsent answers.
import { api, type Json } from "./api";

export type PlayEvent = { event_id: string; segment_id: string; rate: number };
export type PendingAnswer = {
  // attempt_id set: an online attempt whose submission could not be sent (R03); sent to /attempts/{id}/submit
  attempt_id?: string; play_events?: PlayEvent[];
  event_id: string; unit_id: string; unit_revision: number; answers: Record<string, { choice_id: string } | null>; plays: Record<string, number>;
  rates: Record<string, number>; plan_id?: string | null; slot_id?: string | null; assignment_version?: number | null; client_started_at: string; client_answered_at: string; title: string;
};
export type Pack = { slot_id: string; plan_id: string; idx: number; unit_id: string; unit_revision: number; assignment_version: number; reason_text: string; est_sec: number; card: Json; unit: Json; saved_at: string };

function open(userId: string): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(`lq-${userId}`, 1);
    req.onupgradeneeded = () => {
      req.result.createObjectStore("outbox", { keyPath: "event_id" });
      req.result.createObjectStore("packs", { keyPath: "slot_id" });
      req.result.createObjectStore("kv");
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function tx<T>(userId: string, store: string, mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest): Promise<T> {
  const db = await open(userId);
  return new Promise((resolve, reject) => {
    const t = db.transaction(store, mode);
    const req = fn(t.objectStore(store));
    t.oncomplete = () => { db.close(); resolve(req.result as T); };
    t.onerror = () => { db.close(); reject(t.error); };
  });
}

export const putPending = (u: string, a: PendingAnswer) => tx<void>(u, "outbox", "readwrite", (s) => s.put(a));
export const listPending = (u: string) => tx<PendingAnswer[]>(u, "outbox", "readonly", (s) => s.getAll());
export const removePending = (u: string, id: string) => tx<void>(u, "outbox", "readwrite", (s) => s.delete(id));
export const putPack = (u: string, p: Pack) => tx<void>(u, "packs", "readwrite", (s) => s.put(p));
export const listPacks = (u: string) => tx<Pack[]>(u, "packs", "readonly", (s) => s.getAll());
export const getPack = (u: string, slotId: string) => tx<Pack | undefined>(u, "packs", "readonly", (s) => s.get(slotId));
export const removePack = (u: string, slotId: string) => tx<void>(u, "packs", "readwrite", (s) => s.delete(slotId));
export const kvSet = (u: string, k: string, v: Json) => tx<void>(u, "kv", "readwrite", (s) => s.put(v, k));
export const kvGet = <T,>(u: string, k: string) => tx<T | undefined>(u, "kv", "readonly", (s) => s.get(k));
export const kvDel = (u: string, k: string) => tx<void>(u, "kv", "readwrite", (s) => s.delete(k));

const cacheName = (u: string) => `lq-audio-${u}`;

async function sha256Hex(buf: ArrayBuffer): Promise<string> {
  const d = await crypto.subtle.digest("SHA-256", buf);
  return Array.from(new Uint8Array(d), (b) => b.toString(16).padStart(2, "0")).join("");
}

/** Download one audio file completely and keep it only if size and hash match. */
export async function downloadAudio(u: string, url: string, expect: { bytes?: number | null; sha256?: string | null }): Promise<void> {
  if (navigator.storage?.estimate) {
    const { quota = 0, usage = 0 } = await navigator.storage.estimate();
    if (expect.bytes && quota && quota - usage < expect.bytes * 1.2) throw new Error("端末の空き容量が足りません。不要なダウンロードを削除してください。");
  }
  const res = await fetch(url, { credentials: "same-origin" });
  if (!res.ok) throw new Error(`ダウンロードに失敗しました (${res.status})`);
  const buf = await res.arrayBuffer();
  if (expect.bytes && buf.byteLength !== expect.bytes) throw new Error("ダウンロードが途中で切れました。もう一度お試しください。");
  if (expect.sha256 && crypto.subtle && (await sha256Hex(buf)) !== expect.sha256) throw new Error("ダウンロードしたデータが壊れています。もう一度お試しください。");
  const cache = await caches.open(cacheName(u));
  try {
    await cache.put(url, new Response(buf, { headers: { "Content-Type": res.headers.get("Content-Type") || "audio/mpeg", "X-LQ-Bytes": String(buf.byteLength), "X-LQ-SHA256": expect.sha256 || "" } }));
  } catch {
    throw new Error("端末に保存できませんでした（容量不足の可能性があります）。");
  }
  navigator.storage?.persist?.().catch(() => {});
}

/** "ok" only when the stored copy is complete and intact; browsers may evict caches at any time. */
export async function audioState(u: string, url: string, expect: { bytes?: number | null; sha256?: string | null }): Promise<"none" | "ok" | "broken"> {
  if (typeof caches === "undefined") return "none";
  const hit = await (await caches.open(cacheName(u))).match(url);
  if (!hit) return "none";
  const buf = await hit.arrayBuffer();
  if (expect.bytes && buf.byteLength !== expect.bytes) return "broken";
  if (expect.sha256 && crypto.subtle && (await sha256Hex(buf)) !== expect.sha256) return "broken";
  return "ok";
}

export async function removeAudio(u: string, url: string): Promise<void> {
  await (await caches.open(cacheName(u))).delete(url);
}

/** A playable URL: the verified local copy if there is one (works offline, seekable), else the network URL. */
export async function playableUrl(u: string, url: string): Promise<{ src: string; local: boolean }> {
  if (typeof caches !== "undefined") {
    const hit = await (await caches.open(cacheName(u))).match(url);
    if (hit) return { src: URL.createObjectURL(await hit.blob()), local: true };
  }
  return { src: url, local: false };
}

export async function syncPending(u: string): Promise<{ sent: number; results: Json[] }> {
  const all = await listPending(u);
  if (!all.length) return { sent: 0, results: [] };
  // online attempts whose submit did not get through: submit them now, with every playback event (idempotent)
  const direct: Json[] = [];
  for (const it of all.filter((x) => x.attempt_id)) {
    try {
      await api(`/attempts/${it.attempt_id}/submit`, { body: { answers: it.answers, plays: it.play_events ?? [] } });
      await removePending(u, it.event_id); await kvDel(u, `draft:${it.attempt_id}`);
      direct.push({ event_id: it.event_id, status: "synced", title: it.title });
    } catch (e) {
      const st = (e as { status?: number; offline?: boolean });
      if (st.offline) throw e;
      if (st.status === 409 || st.status === 403 || st.status === 404) { await removePending(u, it.event_id); direct.push({ event_id: it.event_id, status: "rejected", title: it.title }); }
    }
  }
  const items = all.filter((x) => !x.attempt_id);
  if (!items.length) {
    const done = (await kvGet<Json[]>(u, "synced")) || [];
    await kvSet(u, "synced", [...direct.map((r) => ({ ...r, at: new Date().toISOString() })), ...done].slice(0, 30));
    return { sent: direct.length, results: direct };
  }
  const out = await api<{ results: Json[] }>("/sync/answers", { body: { items: items.map(({ title: _t, attempt_id: _a, play_events: _p, ...rest }) => rest) } });
  for (const r of out.results) {
    if (r.status === "synced" || r.status === "already_synced" || r.status === "rejected") {
      const it = items.find((x) => x.event_id === r.event_id);
      await removePending(u, r.event_id);
      if (it?.slot_id) await removePack(u, it.slot_id);
    }
  }
  const done = (await kvGet<Json[]>(u, "synced")) || [];
  await kvSet(u, "synced", [...out.results.map((r) => ({ ...r, title: items.find((x) => x.event_id === r.event_id)?.title, at: new Date().toISOString() })), ...done].slice(0, 30));
  return { sent: out.results.length, results: out.results };
}

/** Remove everything this user stored on the device (logout). The caller warns about unsent answers first. */
export async function clearDevice(u: string): Promise<void> {
  if (typeof caches !== "undefined") await caches.delete(cacheName(u));
  await new Promise<void>((resolve) => {
    const req = indexedDB.deleteDatabase(`lq-${u}`);
    req.onsuccess = req.onerror = req.onblocked = () => resolve();
  });
  for (let i = sessionStorage.length - 1; i >= 0; i--) {
    const k = sessionStorage.key(i);
    if (k?.startsWith("lq:")) sessionStorage.removeItem(k);
  }
}
