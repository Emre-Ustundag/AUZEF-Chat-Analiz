import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // npm workspaces hoists node_modules to the repo root. Without this, the
  // standalone trace starts at apps/web and misses the hoisted dependencies.
  outputFileTracingRoot: path.join(__dirname, "../.."),
};

export default nextConfig;
