import { createHash, randomInt, randomUUID } from "node:crypto";
import { spawn, spawnSync } from "node:child_process";
import {
  lstatSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  renameSync,
  unlinkSync,
  writeFileSync
} from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const eventName = process.argv[2] || "Unknown";
const chunks = [];
for await (const chunk of process.stdin) chunks.push(Buffer.from(chunk));
const raw = Buffer.concat(chunks).toString("utf8") || "{}";
const fallback = {};
const pluginRoot = dirname(dirname(fileURLToPath(import.meta.url)));

function agentrootsRuntime() {
  const path = process.env.AGENTROOTS_RUNTIME_FILE || join(pluginRoot, "runtime.json");
  try {
    const value = JSON.parse(readFileSync(path, "utf8"));
    if (!value || typeof value.python !== "string" || !value.python.trim()) return null;
    return value;
  } catch {
    return null;
  }
}

const runtime = agentrootsRuntime();

function agentrootsInvocation(args) {
  const environment = { ...process.env };
  const mappings = {
    db: "AGENTROOTS_DB",
    config: "AGENTROOTS_CONFIG",
    project_registry: "AGENTROOTS_PROJECT_REGISTRY",
    hook_runtime: "AGENTROOTS_HOOK_RUNTIME",
    hook_spool: "AGENTROOTS_HOOK_SPOOL",
    model_cache: "AGENTROOTS_MODEL_CACHE"
  };
  for (const [key, name] of Object.entries(mappings)) {
    if (!environment[name] && typeof runtime?.[key] === "string" && runtime[key]) {
      environment[name] = runtime[key];
    }
  }
  if (runtime) {
    return {
      command: runtime.python,
      args: ["-m", "agentroots.cli", ...args],
      environment
    };
  }
  return { command: "agentroots", args, environment };
}

function codexOutput(value) {
  const output = {};
  if (value?.systemMessage) output.systemMessage = value.systemMessage;
  const specific = value?.hookSpecificOutput;
  const contextEvents = new Set([
    "SessionStart",
    "SubagentStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse"
  ]);
  if (specific?.additionalContext && contextEvents.has(eventName)) {
    output.hookSpecificOutput = {
      hookEventName: eventName,
      additionalContext: specific.additionalContext
    };
  }
  return output;
}

function normalizedPayload() {
  try {
    const value = JSON.parse(raw);
    return {
      ...value,
      hook_event_name: value.hook_event_name || eventName,
      event_id: value.event_id || `he_${randomUUID().replaceAll("-", "")}`,
      timestamp: value.timestamp || new Date().toISOString()
    };
  } catch {
    return { hook_event_name: eventName, event_id: `he_${randomUUID().replaceAll("-", "")}` };
  }
}

const normalized = normalizedPayload();
const payload = JSON.stringify(normalized);

const secretRules = [
  [
    /-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----/gi,
    "[REDACTED PRIVATE KEY]"
  ],
  [
    /\b((?:[a-z0-9]+[_-])*(?:api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|token|client[_-]?secret|secret[_-]?access[_-]?key|account[_-]?key|private[_-]?key|password|passwd|pwd|secret|cookie))['"]?\s*[:=]\s*['"]?([^\s'",;]+)/gi,
    "$1=[REDACTED]"
  ],
  [/\b(authorization\s*[:=]\s*bearer|bearer)\s+[A-Za-z0-9._~+/=-]{8,}/gi, "$1 [REDACTED]"],
  [/\b([a-z][a-z0-9+.-]{1,20}:\/\/[^:\s/@]+:)[^@\s/]+(@)/gi, "$1[REDACTED]$2"],
  [/\b(?:gh[pousr][_-][A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,})\b/g, "[REDACTED]"],
  [/\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{16,}\b/g, "[REDACTED]"],
  [/\b(?:hf_|npm_)[A-Za-z0-9]{20,}\b/g, "[REDACTED]"],
  [/\bxox[baprs]-[A-Za-z0-9-]{16,}\b/g, "[REDACTED]"],
  [/\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b/g, "[REDACTED]"],
  [/\bAIza[0-9A-Za-z_-]{30,}\b/g, "[REDACTED]"],
  [/\b(?:AKIA|ASIA)[A-Z0-9]{16}\b/g, "[REDACTED]"],
  [/\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b/g, "[REDACTED]"]
];

const injectionRules = [
  /ignore (?:all )?(?:previous|prior|earlier) instructions/i,
  /(?:reveal|print|repeat|expose|leak) (?:the )?(?:system|developer) prompt/i,
  /(?:system|developer) (?:message|prompt|instructions?)/i,
  /(?:execute|run) (?:this )?(?:command|shell|tool)/i,
  /you are now (?:a|an|the) /i,
  /override (?:your|the) (?:rules|policy|instructions)/i,
  /<\/?(?:system|developer|assistant|tool)>/i,
  /\[(?:system|inst)\]/i
];

const sensitiveFieldNames = new Set([
  "api_key",
  "apikey",
  "access_token",
  "auth_token",
  "authorization",
  "bearer",
  "client_secret",
  "cookie",
  "password",
  "passwd",
  "private_key",
  "pwd",
  "refresh_token",
  "secret",
  "set_cookie",
  "token"
]);

function sensitiveFieldName(value) {
  const normalized = String(value).toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  if (sensitiveFieldNames.has(normalized)) return true;
  return [
    "_api_key",
    "_access_token",
    "_auth_token",
    "_client_secret",
    "_private_key",
    "_refresh_token",
    "_secret_access_key"
  ].some((suffix) => normalized.endsWith(suffix));
}

function redactScalar(value) {
  let text = String(value ?? "");
  let redacted = false;
  for (const [pattern, replacement] of secretRules) {
    const next = text.replace(pattern, replacement);
    redacted ||= next !== text;
    text = next;
  }
  const injectionRisk = injectionRules.some((pattern) => pattern.test(text));
  return { value: text, redacted, injectionRisk };
}

function sanitizeStructured(value, depth = 0) {
  if (typeof value === "string") return redactScalar(value);
  if (value === null || value === undefined || typeof value !== "object" || depth >= 16) {
    return { value, redacted: false, injectionRisk: false };
  }
  if (Array.isArray(value)) {
    const results = value.map((item) => sanitizeStructured(item, depth + 1));
    return {
      value: results.map((item) => item.value),
      redacted: results.some((item) => item.redacted),
      injectionRisk: results.some((item) => item.injectionRisk)
    };
  }

  const output = Object.create(null);
  let redacted = false;
  let injectionRisk = false;
  for (const [key, nested] of Object.entries(value)) {
    const result = sanitizeStructured(nested, depth + 1);
    const sensitive = sensitiveFieldName(key);
    output[key] = sensitive && nested !== null ? "[REDACTED]" : result.value;
    redacted ||= result.redacted || sensitive;
    injectionRisk ||= result.injectionRisk;
  }
  return { value: output, redacted, injectionRisk };
}

function redactText(value, maxChars) {
  const sanitized = sanitizeStructured(value);
  let text;
  if (typeof sanitized.value === "string") text = sanitized.value;
  else {
    try {
      text = JSON.stringify(sanitized.value);
    } catch {
      text = String(sanitized.value ?? "");
    }
  }
  const truncated = text.length > maxChars;
  return {
    text: text.slice(0, maxChars),
    redacted: sanitized.redacted,
    injectionRisk: sanitized.injectionRisk,
    truncated
  };
}

function digest(value) {
  return createHash("sha256").update(String(value ?? "")).digest("hex").slice(0, 24);
}

function fullDigest(value) {
  return createHash("sha256").update(String(value ?? "")).digest("hex");
}

function first(value, names) {
  for (const name of names) {
    if (value[name] !== undefined && value[name] !== null && value[name] !== "") return value[name];
  }
  return "";
}

function safeProjectHint(value) {
  const leaf = String(value || "").split(/[\\/]/).filter(Boolean).at(-1) || "default";
  return leaf.toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "") || "default";
}

function projectSlug(value) {
  const leaf = String(value || "").split(/[\\/]/).filter(Boolean).at(-1) || "project";
  return leaf.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48)
    || "project";
}

function normalizedPath(value) {
  const normalized = String(value || "").replaceAll("\\", "/").replace(/\/+$/, "");
  const windowsPath = /^[A-Za-z]:\//.test(normalized)
    || normalized.startsWith("//");
  return windowsPath ? normalized.toLowerCase() : normalized;
}

function normalizedRemote(value) {
  const remote = String(value || "").trim();
  if (!remote) return "";
  let host = "";
  let remotePath = "";
  let port = "";
  let scheme = "";
  if (remote.includes("://")) {
    try {
      const parsed = new URL(remote);
      host = parsed.hostname;
      remotePath = parsed.pathname;
      port = parsed.port;
      scheme = parsed.protocol.replace(/:$/, "").toLowerCase();
    } catch {
      return remote.replace(/\/+$/, "").replace(/\.git$/, "").toLowerCase();
    }
  } else {
    const scp = remote.match(/^(?:[^/@:]+@)?([^/:]+):(.+)$/);
    if (scp && !/^[A-Za-z]:[\\/]/.test(remote)) {
      [, host, remotePath] = scp;
      scheme = "ssh";
    } else {
      return remote.replace(/\/+$/, "").replace(/\.git$/, "").toLowerCase();
    }
  }
  const cleanPath = remotePath.replace(/\/+/g, "/").replace(/^\/+|\/+$/g, "")
    .replace(/\.git$/, "");
  const defaultPort = (["ssh", "git+ssh"].includes(scheme) && port === "22")
    || (scheme === "https" && port === "443")
    || (scheme === "http" && port === "80");
  const authority = port && !defaultPort ? `${host}:${port}` : host;
  if (!authority || !cleanPath) {
    return remote.replace(/\/+$/, "").replace(/\.git$/, "").toLowerCase();
  }
  return `https://${authority}/${cleanPath}`.toLowerCase();
}

function legacyNormalizedRemote(value) {
  return String(value || "").trim().toLowerCase().replace(/\.git$/, "");
}

function remoteFingerprints(value) {
  return [...new Set([normalizedRemote(value), legacyNormalizedRemote(value)].filter(Boolean))]
    .map((item) => fullDigest(item));
}

function remoteLeaf(value) {
  const normalized = normalizedRemote(value);
  return normalized.split("/").at(-1)?.split(":").at(-1) || "project";
}

function gitValue(cwd, args) {
  try {
    const result = spawnSync("git", ["-C", cwd, ...args], {
      encoding: "utf8",
      timeout: 1500,
      windowsHide: true,
      stdio: ["ignore", "pipe", "ignore"]
    });
    return result.status === 0 ? String(result.stdout || "").trim() : "";
  } catch {
    return "";
  }
}

function projectRegistryPath() {
  if (process.env.AGENTROOTS_PROJECT_REGISTRY) return process.env.AGENTROOTS_PROJECT_REGISTRY;
  const database = process.env.AGENTROOTS_DB || process.env.RESEARCH_STATE_DB;
  if (database) return join(dirname(database), "projects.json");
  return process.platform === "win32"
    ? join(dataRoot(), "agentroots", "agentroots", "projects.json")
    : join(dataRoot(), "agentroots", "projects.json");
}

function registeredProject(pathFingerprint, remoteHashes) {
  try {
    const registry = JSON.parse(readFileSync(projectRegistryPath(), "utf8"));
    for (const [projectId, project] of Object.entries(registry.projects || {})) {
      if (!/^[A-Za-z0-9._-]{1,96}$/.test(projectId) || !project || typeof project !== "object") {
        continue;
      }
      if (remoteHashes.length) {
        if (remoteHashes.some((item) => (project.remote_fingerprints || []).includes(item))) {
          return projectId;
        }
      } else if ((project.path_fingerprints || []).includes(pathFingerprint)) return projectId;
    }
  } catch {}
  return "";
}

function safeProjectId(cwdValue) {
  const configured = String(process.env.AGENTROOTS_PROJECT || "");
  if (/^[A-Za-z0-9._-]{1,96}$/.test(configured)) return configured;

  const cwd = String(cwdValue || process.cwd());
  const root = gitValue(cwd, ["rev-parse", "--show-toplevel"]) || cwd;
  const remote = gitValue(root, ["remote", "get-url", "origin"]);
  const pathFingerprint = fullDigest(normalizedPath(root));
  const remoteHashes = remoteFingerprints(remote);
  const registered = registeredProject(pathFingerprint, remoteHashes);
  if (registered) return registered;
  const identity = normalizedRemote(remote) || normalizedPath(root);
  return `${projectSlug(remote ? remoteLeaf(remote) : root)}-${fullDigest(identity).slice(0, 10)}`;
}

function safeEventName(value) {
  const candidate = String(value || eventName);
  if (/^[A-Za-z][A-Za-z0-9_.-]{0,63}$/.test(candidate)) return candidate;
  return /^[A-Za-z][A-Za-z0-9_.-]{0,63}$/.test(eventName) ? eventName : "Unknown";
}

function safeTimestamp(value) {
  const parsed = new Date(String(value || ""));
  return Number.isNaN(parsed.valueOf()) ? new Date().toISOString() : parsed.toISOString();
}

function boundedInt(name, fallback, minimum, maximum) {
  const parsed = Number.parseInt(process.env[name] || "", 10);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(maximum, Math.max(minimum, parsed));
}

function sanitizedSpoolPayload(value) {
  const fields = {
    prompt: [first(value, ["prompt", "user_prompt", "userPrompt", "message"]), 2048],
    tool_name: [first(value, ["tool_name", "toolName", "tool"]), 128],
    tool_input: [first(value, ["tool_input", "toolInput", "input", "arguments"]), 3072],
    tool_response: [first(value, ["tool_response", "toolResponse", "output", "result"]), 3072],
    error: [value.error, 1024],
    last_assistant_message: [
      first(value, ["last_assistant_message", "lastAssistantMessage", "response"]),
      2048
    ]
  };
  const delta = {};
  let redacted = false;
  let injectionRisk = false;
  let truncated = false;
  for (const [name, [fieldValue, limit]] of Object.entries(fields)) {
    if (fieldValue === undefined || fieldValue === null || fieldValue === "") continue;
    const result = redactText(fieldValue, limit);
    delta[name] = result.text;
    redacted ||= result.redacted;
    injectionRisk ||= result.injectionRisk;
    truncated ||= result.truncated;
  }

  const sourceEvent = first(value, ["event_id", "eventId"]);
  const sourceSession = first(value, ["session_id", "sessionId", "thread_id", "threadId"]);
  const sourceCwd = first(value, ["cwd", "workdir", "working_directory"]);
  const sourceTimestamp = String(value.timestamp || "");
  return {
    spool_schema: 1,
    hook_event_name: safeEventName(value.hook_event_name),
    event_id: `spooled_${digest(sourceEvent || payload)}`,
    session_id: `spooled_${digest(sourceSession || "unknown")}`,
    project_id: safeProjectId(sourceCwd),
    cwd: safeProjectHint(sourceCwd),
    timestamp: safeTimestamp(sourceTimestamp),
    ...delta,
    spool_metadata: {
      source_payload_hash: digest(payload),
      redacted,
      injection_risk: injectionRisk,
      truncated
    }
  };
}

function dataRoot() {
  return process.platform === "win32"
    ? (process.env.LOCALAPPDATA || join(homedir(), "AppData", "Local"))
    : (process.env.XDG_DATA_HOME || join(homedir(), ".local", "share"));
}

function runtimeDir() {
  if (process.env.AGENTROOTS_HOOK_RUNTIME) return process.env.AGENTROOTS_HOOK_RUNTIME;
  // platformdirs uses app name as app author on Windows, producing a double segment.
  return process.platform === "win32"
    ? join(dataRoot(), "agentroots", "agentroots", "hooks")
    : join(dataRoot(), "agentroots", "hooks");
}

function spool() {
  try {
    const dir = process.env.AGENTROOTS_HOOK_SPOOL || join(runtimeDir(), "spool");
    mkdirSync(dir, { recursive: true });
    const maxFiles = boundedInt("AGENTROOTS_HOOK_SPOOL_MAX_FILES", 64, 1, 512);
    const maxFileBytes = boundedInt("AGENTROOTS_HOOK_SPOOL_MAX_FILE_BYTES", 16384, 2048, 65536);
    const maxTotalBytes = boundedInt(
      "AGENTROOTS_HOOK_SPOOL_MAX_TOTAL_BYTES", 524288, maxFileBytes, 4194304
    );
    const spoolPayload = sanitizedSpoolPayload(normalized);
    let encoded = JSON.stringify(spoolPayload);
    if (Buffer.byteLength(encoded, "utf8") > maxFileBytes) {
      spoolPayload.spool_metadata.truncated = true;
      for (const key of ["tool_response", "tool_input", "last_assistant_message", "prompt", "error"]) {
        if (!(key in spoolPayload)) continue;
        spoolPayload[key] = String(spoolPayload[key]).slice(0, 256);
        encoded = JSON.stringify(spoolPayload);
        if (Buffer.byteLength(encoded, "utf8") <= maxFileBytes) break;
      }
    }
    if (Buffer.byteLength(encoded, "utf8") > maxFileBytes) return;

    const existing = readdirSync(dir)
      .filter((name) => /^hook-.*\.json$/.test(name))
      .map((name) => {
        const path = join(dir, name);
        const stat = lstatSync(path);
        return { path, bytes: stat.size, mtime: stat.mtimeMs };
      })
      .sort((left, right) => left.mtime - right.mtime);
    let totalBytes = existing.reduce((total, item) => total + item.bytes, 0);
    while (
      existing.length >= maxFiles || totalBytes + Buffer.byteLength(encoded, "utf8") > maxTotalBytes
    ) {
      const oldest = existing.shift();
      if (!oldest) return;
      unlinkSync(oldest.path);
      totalBytes -= oldest.bytes;
    }

    const stem = `${Date.now()}-${process.pid}-${randomInt(1000, 9999)}`;
    const temporary = join(dir, `.tmp-${stem}.json`);
    const finalPath = join(dir, `hook-${stem}.json`);
    writeFileSync(temporary, encoded, { encoding: "utf8", mode: 0o600, flag: "wx" });
    renameSync(temporary, finalPath);

    const afterWrite = readdirSync(dir)
      .filter((name) => /^hook-.*\.json$/.test(name))
      .map((name) => {
        const path = join(dir, name);
        const stat = lstatSync(path);
        return { path, bytes: stat.size, mtime: stat.mtimeMs };
      })
      .sort((left, right) => left.mtime - right.mtime);
    let afterBytes = afterWrite.reduce((total, item) => total + item.bytes, 0);
    while (afterWrite.length > maxFiles || afterBytes > maxTotalBytes) {
      const oldest = afterWrite.shift();
      if (!oldest) break;
      unlinkSync(oldest.path);
      afterBytes -= oldest.bytes;
    }
  } catch {}
}

if (eventName === "SessionStart") {
  try {
    const invocation = agentrootsInvocation(["hook-daemon-start"]);
    const child = spawn(invocation.command, invocation.args, {
      detached: true,
      stdio: "ignore",
      windowsHide: true,
      env: invocation.environment
    });
    child.unref();
  } catch {}
}

// Normal path: talk to the resident daemon directly. Avoid starting Python for every hook.
try {
  const token = readFileSync(join(runtimeDir(), "daemon.token"), "utf8").trim();
  const port = Number(process.env.AGENTROOTS_HOOK_PORT || "37623");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 2200);
  const response = await fetch(`http://127.0.0.1:${port}/hook`, {
    method: "POST",
    headers: { "content-type": "application/json", authorization: `Bearer ${token}` },
    body: JSON.stringify({ payload: normalized, event_name: eventName }),
    signal: controller.signal
  });
  clearTimeout(timer);
  if (response.ok) {
    process.stdout.write(JSON.stringify(codexOutput(await response.json())));
    process.exit(0);
  }
} catch {}

// Fail-open recovery path for first start or a crashed daemon.
const invocation = agentrootsInvocation(["hook", "--event", eventName]);
const result = spawnSync(invocation.command, invocation.args, {
  input: payload,
  encoding: "utf8",
  timeout: eventName === "Stop" ? 7000 : 4200,
  windowsHide: true,
  stdio: ["pipe", "pipe", "ignore"],
  env: invocation.environment
});

if (result.status === 0 && String(result.stdout || "").trim()) {
  try {
    const parsed = JSON.parse(result.stdout);
    process.stdout.write(JSON.stringify(codexOutput(parsed)));
    process.exit(0);
  } catch {}
}

spool();
process.stdout.write(JSON.stringify(fallback));
