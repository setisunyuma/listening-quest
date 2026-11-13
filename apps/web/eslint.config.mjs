import next from "eslint-config-next";

const config = [
  ...next,
  { ignores: [".next/**", "node_modules/**", "src/lib/api-types.ts", "public/sw.js", "playwright-report/**", "test-results/**"] },
  { rules: { "react-hooks/set-state-in-effect": "off", "react-hooks/exhaustive-deps": "off" } },
];
export default config;
