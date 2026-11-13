// Same-origin pass-through to FastAPI for setups without a reverse proxy (local dev, tests).
// No business logic here: it forwards the request and streams the answer back unchanged.
// API_INTERNAL_URL is read at run time. Behind Caddy (production) /api/* never reaches Next.js.
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const PASS_REQ = ["cookie", "content-type", "range", "x-csrf-token", "authorization", "origin", "accept", "if-none-match", "x-forwarded-for"];
const DROP_RES = new Set(["content-encoding", "transfer-encoding", "connection", "keep-alive", "set-cookie"]);

async function handle(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }): Promise<Response> {
  const { path } = await ctx.params;
  const base = (process.env.API_INTERNAL_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
  const url = `${base}/api/${path.map(encodeURIComponent).join("/")}${req.nextUrl.search}`;
  const headers = new Headers();
  for (const h of PASS_REQ) {
    const v = req.headers.get(h);
    if (v) headers.set(h, v);
  }
  const hasBody = !["GET", "HEAD"].includes(req.method);
  let upstream: Response;
  try {
    upstream = await fetch(url, { method: req.method, headers, body: hasBody ? req.body : undefined, redirect: "manual", cache: "no-store", ...(hasBody ? { duplex: "half" } : {}) } as RequestInit);
  } catch {
    return Response.json({ detail: "学習サーバーに接続できません。" }, { status: 502 });
  }
  const out = new Headers();
  upstream.headers.forEach((v, k) => { if (!DROP_RES.has(k)) out.set(k, v); });
  for (const c of upstream.headers.getSetCookie()) out.append("set-cookie", c);
  return new Response(req.method === "HEAD" ? null : upstream.body, { status: upstream.status, headers: out });
}

export { handle as GET, handle as POST, handle as PATCH, handle as PUT, handle as DELETE, handle as HEAD };
