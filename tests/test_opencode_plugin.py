from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


def test_opencode_plugin_callbacks_inject_context_and_show_toasts(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")

    source = Path(__file__).parents[1] / "plugins" / "opencode" / "agentroots.js"
    plugin = tmp_path / "plugins" / "agentroots.js"
    plugin.parent.mkdir(parents=True)
    plugin.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    package = tmp_path / "node_modules" / "@opencode-ai" / "plugin"
    package.mkdir(parents=True)
    (package / "package.json").write_text(
        json.dumps({"name": "@opencode-ai/plugin", "type": "module", "exports": "./index.js"}),
        encoding="utf-8",
    )
    (package / "index.js").write_text(
        """
const stringSchema = { describe() { return this } }
export const tool = Object.assign((definition) => definition, {
  schema: { string: () => ({ ...stringSchema }) },
})
""".strip(),
        encoding="utf-8",
    )

    runner = tmp_path / "runner.mjs"
    runner.write_text(
        """
import { pathToFileURL } from "node:url"

const calls = []
const response = {
  agentrootsProject: "demo",
  hookSpecificOutput: { additionalContext: "Prior accepted fact: batch size 4096 failed." },
  agentrootsNotification: { message: "fetched 1 relevant root" },
}
globalThis.Bun = {
  spawn(command) {
    const input = []
    calls.push({ command, input })
    return {
      stdin: { write(value) { input.push(String(value)) }, end() {} },
      stdout: new Response(JSON.stringify(response)).body,
      stderr: new Response("").body,
      exited: Promise.resolve(0),
    }
  },
}

const { AgentRootsPlugin } = await import(pathToFileURL(process.argv[2]).href)
const toasts = []
const hooks = await AgentRootsPlugin({
  client: { tui: { showToast: async (value) => toasts.push(value) } },
  directory: process.cwd(),
})
const message = { id: "m1", sessionID: "s1", system: "base" }
await hooks["chat.message"](
  { sessionID: "s1" },
  { message, parts: [{ type: "text", text: "What failed before?" }] },
)
if (!message.system.includes("batch size 4096")) throw new Error("chat context missing")

const system = []
await hooks["experimental.chat.system.transform"]({ sessionID: "s1" }, { system })
if (!system.some((value) => value.includes("batch size 4096"))) {
  throw new Error("system transform context missing")
}

const compacting = { context: [] }
await hooks["experimental.session.compacting"]({ sessionID: "s1" }, compacting)
if (!compacting.context[0].includes("batch size 4096")) {
  throw new Error("compaction context missing")
}

await hooks["tool.execute.before"](
  { sessionID: "s1", tool: "bash" },
  { args: { command: "pytest" } },
)
await hooks["tool.execute.after"](
  { sessionID: "s1", tool: "bash", args: { command: "pytest" } },
  { output: "passed" },
)
await hooks.event({ event: { type: "session.created", properties: { info: { id: "s2" } } } })
await hooks.event({ event: { type: "session.compacted", properties: { sessionID: "s2" } } })
await hooks.event({
  event: { type: "session.error", properties: { sessionID: "s2", error: "failed" } },
})
await hooks.event({ event: { type: "session.idle", properties: { sessionID: "s2" } } })

const contextTool = await hooks.tool.agentroots_context.execute({ query: "batch" })
if (!contextTool.includes("agentrootsProject")) throw new Error("context tool failed")
if (toasts.length < 1) throw new Error("toast missing")
console.log(JSON.stringify({ calls: calls.length, toasts: toasts.length }))
""".strip(),
        encoding="utf-8",
    )

    result = subprocess.run(
        [node, str(runner), str(plugin)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["calls"] >= 10
    assert output["toasts"] >= 1
