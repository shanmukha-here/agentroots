# Support matrix

This matrix separates protocol compatibility from automated harness integration. A model name is
not a harness, and an MCP connection alone does not provide proactive capture or tool-boundary
retrieval.

| Surface | Status | What is covered | Important limit |
|---|---|---|---|
| Local core | Validated alpha | SQLite WAL, FTS5 and fuzzy retrieval, lifecycle review, typed evidence classification and local verification, packets, CLI, stdio MCP | Actor labels are unauthenticated; generic evidence URIs and caller-written test receipts are references, not automatically resolved proof |
| Codex | Validated alpha | MCP, packaged proactive hooks, prompt and tool boundaries, compact recall, candidate extraction | Codex requires `/hooks` review for new or changed definitions; warnings are advisory and do not block tools |
| OpenCode | Experimental | Global plugin, compact toasts, read-only session import, cross-harness episode tests | Provider-backed interactive flow is not yet release-gated |
| Claude and other MCP hosts | MCP-only | Standard stdio tools and resources | No shipped or validated automatic lifecycle hooks |
| DeepSeek and other models | Through a harness | Any compatible MCP host can expose AgentRoots to the model | No standalone model-specific integration |
| Semantic retrieval | Alpha | Base install adds local BGE through FastEmbed and NumPy, with FTS fallback | First model download runs in the background and uses local cache storage |
| GLiNER extraction | Optional | `agentroots[hooks]` adds a local structured extraction fallback | Output remains an untrusted candidate |
| Qwen extraction | Experimental optional | `agentroots[qwen]` plus an explicitly configured model | No bundled weights and no requirement for normal use |
| MLflow | Read-only validated path | Fetch, search, compare, snapshot link, and snapshot revalidation | AgentRoots does not start runs or store artifacts |
| Trackio | Adapter interface | Version-specific fetching can be injected | Not presented as a fully validated service integration |
| Graph viewer | Validated read-only | Offline export, filters, relationships, evidence references, copyable IDs | Direct graph editing remains roadmap work |
| PostgreSQL, remote HTTP, ACLs, Flowcept, AiiDA | Roadmap or interface-only | Design boundaries exist | Not supported backends in v0.2.0 |

## Status definitions

- **Validated alpha** means automated tests plus a real integration smoke have passed. It does not
  imply production support or broad platform coverage.
- **Experimental** means the implementation exists and has focused automated coverage, but the full
  real-client workflow is not a release gate yet.
- **MCP-only** means the portable tool and resource surface works through stdio. The host is
  responsible for when to call it.
- **Interface-only** means an abstraction exists without a fully tested optional backend.

The matrix should be updated only when reproducible validation evidence changes. Do not infer
support for a harness from the model it happens to run.
