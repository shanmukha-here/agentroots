// AgentRoots OpenCode adapter. Generated state stays in AgentRoots, never this plugin.
import { tool } from "@opencode-ai/plugin"
import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

const latest = new Map()
const configRoot = dirname(dirname(fileURLToPath(import.meta.url)))

function agentrootsInvocation(args) {
  let runtime = null
  try {
    runtime = JSON.parse(readFileSync(
      process.env.AGENTROOTS_RUNTIME_FILE || join(configRoot, "agentroots-runtime.json"),
      "utf8",
    ))
  } catch {}
  const env = { ...process.env }
  const mappings = {
    db: "AGENTROOTS_DB",
    config: "AGENTROOTS_CONFIG",
    project_registry: "AGENTROOTS_PROJECT_REGISTRY",
    hook_runtime: "AGENTROOTS_HOOK_RUNTIME",
    hook_spool: "AGENTROOTS_HOOK_SPOOL",
    model_cache: "AGENTROOTS_MODEL_CACHE",
  }
  for (const [key, name] of Object.entries(mappings)) {
    if (!env[name] && typeof runtime?.[key] === "string" && runtime[key]) env[name] = runtime[key]
  }
  return runtime?.python
    ? { command: runtime.python, args: ["-m", "agentroots.cli", ...args], env }
    : { command: "agentroots", args, env }
}

async function runAgentRoots(eventName, payload) {
  try {
    const invocation = agentrootsInvocation(["hook", "--event", eventName])
    const child = Bun.spawn([invocation.command, ...invocation.args], {
      stdin: "pipe",
      stdout: "pipe",
      stderr: "ignore",
      env: invocation.env,
    })
    child.stdin.write(JSON.stringify({
      ...payload,
      hook_event_name: eventName,
      event_id: payload.event_id || crypto.randomUUID(),
    }))
    child.stdin.end()
    const output = await new Response(child.stdout).text()
    if ((await child.exited) !== 0) return null
    return JSON.parse(output)
  } catch {
    return null
  }
}

async function runCommand(args) {
  const invocation = agentrootsInvocation(args)
  const child = Bun.spawn([invocation.command, ...invocation.args], {
    stdin: "ignore", stdout: "pipe", stderr: "pipe", env: invocation.env,
  })
  const output = await new Response(child.stdout).text()
  const error = await new Response(child.stderr).text()
  if ((await child.exited) !== 0) throw new Error(error || "AgentRoots command failed")
  return output
}

function textParts(parts) {
  return (parts || [])
    .filter((part) => part && part.type === "text")
    .map((part) => part.text || "")
    .filter(Boolean)
    .join("\n")
}

export const AgentRootsPlugin = async ({ client, directory }) => {
  const identity = await runAgentRoots("ProjectIdentity", { cwd: directory })
  const project = identity?.agentrootsProject || process.env.AGENTROOTS_PROJECT || directory
    .replace(/[\\/]+$/, "")
    .split(/[\\/]/)
    .pop()
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, "-") || "default"
  const toast = async (notice) => {
    if (!notice?.message) return
    try {
      await client.tui.showToast({
        body: { title: "AgentRoots", message: notice.message, variant: "info", duration: 2200 },
      })
    } catch {}
  }

  const handle = async (eventName, payload) => {
    const result = await runAgentRoots(eventName, { cwd: directory, ...payload })
    const context = result?.hookSpecificOutput?.additionalContext
    const sessionID = payload.session_id || payload.sessionID
    if (context && sessionID) latest.set(sessionID, context)
    await toast(result?.agentrootsNotification)
    return result
  }

  try {
    const invocation = agentrootsInvocation(["hook-daemon-start"])
    Bun.spawn([invocation.command, ...invocation.args], {
      stdin: "ignore", stdout: "ignore", stderr: "ignore", env: invocation.env,
    })
  } catch {}

  return {
    tool: {
      agentroots_context: tool({
        description: "Recall compact, evidence-aware state for the current project.",
        args: { query: tool.schema.string().describe("Current task or question") },
        async execute(args) {
          return runCommand(["context", project, args.query, "--tokens", "500"])
        },
      }),
      agentroots_frontier: tool({
        description: "Show unresolved goals, questions, and experiments for the current project.",
        args: {},
        async execute() {
          return runCommand(["frontier", project])
        },
      }),
      agentroots_record: tool({
        description: "Open one AgentRoots record by ID after compact context identifies it.",
        args: { id: tool.schema.string().describe("AgentRoots record ID") },
        async execute(args) {
          return runCommand(["get", args.id])
        },
      }),
    },

    "chat.message": async (input, output) => {
      const sessionID = input.sessionID || output.message?.sessionID
      const prompt = textParts(output.parts)
      const result = await handle("UserPromptSubmit", {
        session_id: sessionID,
        message_id: output.message?.id,
        prompt,
      })
      const context = result?.hookSpecificOutput?.additionalContext
      if (context && output.message) {
        output.message.system = [output.message.system, context].filter(Boolean).join("\n\n")
      }
    },

    "experimental.chat.system.transform": async (input, output) => {
      const context = input.sessionID ? latest.get(input.sessionID) : null
      if (context && !output.system.includes(context)) output.system.push(context)
    },

    "experimental.session.compacting": async (input, output) => {
      const result = await handle("SessionStart", {
        session_id: input.sessionID,
        source: "compact",
      })
      const context = result?.hookSpecificOutput?.additionalContext
      if (context) output.context.push(context)
    },

    "tool.execute.before": async (input, output) => {
      await handle("PreToolUse", {
        session_id: input.sessionID,
        tool_name: input.tool,
        tool_input: output.args,
      })
    },

    "tool.execute.after": async (input, output) => {
      await handle("PostToolUse", {
        session_id: input.sessionID,
        tool_name: input.tool,
        tool_input: input.args,
        tool_response: output.output,
      })
    },

    event: async ({ event }) => {
      if (event.type === "session.created") {
        await handle("SessionStart", { session_id: event.properties.info.id })
      } else if (event.type === "session.compacted") {
        await handle("PostCompact", { session_id: event.properties.sessionID })
      } else if (event.type === "session.error") {
        await handle("PostToolUseFailure", {
          session_id: event.properties.sessionID,
          error: event.properties.error,
        })
      } else if (event.type === "session.idle") {
        await handle("Stop", { session_id: event.properties.sessionID })
      }
    },
  }
}
