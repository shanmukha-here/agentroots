# Graph viewer

The AgentRoots graph viewer is a human projection of the same versioned graph supplied to agents.
It uses React Flow for interaction and Dagre for automatic wide and tall layouts. Exported maps
bundle their runtime, styles, and graph JSON into one offline HTML file.

## Current behavior

- custom nodes for every AgentRoots record type
- lifecycle-driven styling
- complete text in a details panel
- evidence-reference and relationship inspection, with core or adapter verification status when
  available
- search and type or lifecycle filtering
- automatic fit, pan, zoom, minimap, and layout switching
- selection focus that dims unrelated branches
- visible copy-ID action with confirmation
- responsive light and dark themes
- soft full-node shading by record type, with lifecycle retained as a separate border signal

The viewer is currently read-only. Dragging, connecting, deletion, and direct persistence are
disabled intentionally.

## Customization boundary

Graph data remains the versioned `research_get_graph` JSON contract. Presentation is owned by the
frontend under `frontend/`:

- `src/main.jsx` defines node components, graph interactions, layouts, and the details panel.
- `src/viewer.css` defines themes, shapes, typography, states, relationships, and responsive UI.
- `build.mjs` creates the offline assets packaged under `src/agentroots/assets/`.

Run `npm install` and `npm run build` in `frontend/` after frontend source changes. Generated
assets are committed because installed Python packages need them to export maps without Node or
network access. `node_modules` is never committed.

## Governed editing path

Future editing must submit commands to AgentRoots instead of writing SQLite or changing graph JSON
in the browser. The intended command boundary is:

1. Human edits a draft node or relationship.
2. Viewer submits the command with project ID, actor, record ID, and expected revision.
3. AgentRoots validates lifecycle, evidence-reference rules, project boundaries, and optimistic
   concurrency. Typed adapters may verify supported evidence kinds separately. In the local alpha, the
   actor value is a provenance label, not an authenticated principal.
4. AgentRoots appends an event and updates the current projection.
5. The viewer receives the new graph version.
6. Connected agents receive only an affected-record notification and refresh context when needed.

Candidate commands include `change_status`, `remove_relationship`, and `resolve_contradiction`.
The backend already exposes `research_revise`, `research_link_records`, `research_link_evidence`,
and `research_candidate` for governed revisions, same-project edges, evidence, and extracted-candidate
review. The viewer does not call them yet. The backend remains the sole authority for every mutation.

`research_revise` uses expected revisions and append-only events. Revising accepted knowledge
returns it to provisional status so the changed meaning must be reviewed again.

For larger graphs, clustering and server-side graph scopes can be added without changing the
record or link contract. A separate WebGL overview may be introduced only if real projects exceed
React Flow's useful interactive scale.
