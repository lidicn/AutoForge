// WO-AF-016 (U2): 构建后落 build-manifest.json
// 形如 { "commit", "build_utc", "files": [{"path","sha256"}] }
// 只放 path + sha256 + commit + UTC，不含任何凭据/内网字面量。
import { createHash } from "node:crypto";
import { execSync } from "node:child_process";
import { readdirSync, readFileSync, writeFileSync, statSync } from "node:fs";
import { join, relative, sep, posix } from "node:path";

const distDir = join(process.cwd(), "dist");
const repoRoot = join(process.cwd(), "..");

const sha256 = (buf) => createHash("sha256").update(buf).digest("hex");

function walk(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) out.push(...walk(full));
    else out.push(full);
  }
  return out;
}

let commit = "";
try {
  commit = execSync("git rev-parse HEAD", { cwd: repoRoot }).toString().trim();
} catch {
  commit = "unknown";
}

const files = walk(distDir)
  .map((full) => {
    const rel = relative(distDir, full).split(sep).join("/");
    return { path: rel, sha256: sha256(readFileSync(full)) };
  })
  .sort((a, b) => a.path.localeCompare(b.path))
  .filter((f) => f.path !== "build-manifest.json");

const manifest = { commit, build_utc: new Date().toISOString(), files };
writeFileSync(join(distDir, "build-manifest.json"), JSON.stringify(manifest, null, 2) + "\n", "utf8");
console.log(`build-manifest.json: commit=${commit} files=${files.length}`);
