import { execSync } from "node:child_process";
import path from "node:path";

export default function globalSetup() {
  if (process.env.LQ_E2E_SKIP_PREPARE) return;
  execSync("bash scripts/e2e-prepare.sh", { cwd: path.resolve(__dirname, "../../.."), stdio: "inherit" });
}
