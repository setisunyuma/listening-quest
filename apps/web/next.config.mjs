/** Same-origin setup: the browser only ever talks to this origin. /api/* is forwarded to FastAPI by
 *  src/app/api/[...path]/route.ts (run-time API_INTERNAL_URL); in production Caddy routes /api/*
 *  straight to the API container (see infra/Caddyfile). */

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  reactStrictMode: true,
  poweredByHeader: false,
  async headers() {
    return [
      { source: "/sw.js", headers: [{ key: "Cache-Control", value: "no-cache" }, { key: "Service-Worker-Allowed", value: "/" }] },
      { source: "/(.*)", headers: [{ key: "X-Content-Type-Options", value: "nosniff" }, { key: "Referrer-Policy", value: "same-origin" }, { key: "X-Frame-Options", value: "DENY" }] },
    ];
  },
};
export default nextConfig;
