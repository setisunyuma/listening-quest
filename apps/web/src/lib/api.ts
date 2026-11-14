// Typed API access. Types come from the OpenAPI contract (npm run contracts) so a change on the
// Python side that the web app does not follow fails type checking.
import type { components } from "./api-types";

export type Json = any;  
// Free-form dictionaries in the contract (`{[key: string]: unknown}`) are treated as loose JSON;
// every declared field keeps its generated type.
type Loose<T> = T extends (infer U)[] ? Loose<U>[] : T extends object ? (string extends keyof T ? Json : { [K in keyof T]: Loose<T[K]> }) : T;
export type S = components["schemas"];
export type Me = Loose<S["MeOut"]>;
export type Attempt = Loose<S["AttemptOut"]>;
export type LearnerUnit = Loose<S["LearnerUnit"]>;
export type LearnerQuestion = Loose<S["LearnerQuestion"]>;
export type Result = Loose<S["ResultOut"]>;
export type ResultQuestion = Loose<S["ResultQuestion"]>;
export type Plan = Loose<S["PlanOut"]>;
export type PlanSlot = Loose<S["PlanSlotOut"]>;
export type Home = Loose<S["HomeOut"]>;
export type LibraryCard = Loose<S["LibraryCard"]>;
export type Ack = Loose<S["AckOut"]>;
export type Answer = { choice_id: string } | null;

export class ApiError extends Error {
  constructor(public status: number, public detail: string, public offline = false) {
    super(detail);
  }
}

function csrf(): string {
  if (typeof document === "undefined") return "";
  const m = document.cookie.match(/(?:^|; )lq_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

export async function api<T = Json>(path: string, opts: { method?: string; body?: unknown; form?: FormData } = {}): Promise<T> {
  const method = opts.method || (opts.body !== undefined || opts.form ? "POST" : "GET");
  const headers: Record<string, string> = {};
  if (method !== "GET") headers["X-CSRF-Token"] = csrf();
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  let res: Response;
  try {
    res = await fetch(`/api${path}`, { method, headers, credentials: "same-origin", body: opts.form ?? (opts.body !== undefined ? JSON.stringify(opts.body) : undefined) });
  } catch {
    throw new ApiError(0, "通信できません。オフラインの可能性があります。", true);
  }
  if (res.status === 401 && typeof window !== "undefined" && !path.startsWith("/auth/")) window.dispatchEvent(new Event("lq:unauthorized"));
  if (!res.ok) {
    let detail = `エラー (${res.status})`;
    try {
      const j = await res.json();
      if (typeof j.detail === "string") detail = j.detail;
      else if (Array.isArray(j.detail)) detail = j.detail.map((d: Json) => d.msg).join(" / ");
    } catch {}
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export const uid = (): string => (crypto.randomUUID ? crypto.randomUUID().replace(/-/g, "") : Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, "0")).join(""));

export const fmtMin = (sec: number): string => (sec < 60 ? `${Math.max(1, Math.round(sec))}秒` : `約${Math.round(sec / 60)}分`);
export const fmtClock = (ms: number): string => {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};

export const MODE_LABEL: Record<string, string> = { common_test: "共通テスト", eiken_pre1: "英検準1級", eiken_1: "英検1級", cefr_c1: "C1", cefr_c2: "C2" };
export const MODES = Object.keys(MODE_LABEL);
export const SKILL_LABEL: Record<string, string> = {
  main_idea: "主旨", explicit_detail: "明示的な細部", numbers_negation: "数字・日時・否定", condition_matching: "条件照合",
  speaker_attitude: "話者の意図・態度", inference: "推論", discourse_structure: "談話構造", integration: "複数情報の統合",
};
export const CATEGORY_LABEL: Record<string, string> = { review: "復習", weak: "弱点練習", explore: "範囲を広げる", radio: "ラジオ", practice: "実践課題" };
