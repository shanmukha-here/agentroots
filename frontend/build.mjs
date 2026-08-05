import { build } from "esbuild";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const output = new URL("../src/agentroots/assets/", import.meta.url);
await mkdir(output, { recursive: true });
await build({
  entryPoints: [fileURLToPath(new URL("src/main.jsx", import.meta.url))],
  bundle: true,
  minify: true,
  format: "iife",
  target: ["es2020"],
  outfile: fileURLToPath(new URL("graph-viewer.js", output)),
  legalComments: "eof",
});
