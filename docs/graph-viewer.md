# Graph viewer

The AgentRoots graph viewer is a human projection of the same versioned graph supplied to agents.
It uses React Flow for interaction and Dagre for automatic wide and tall layouts. Exported maps
bundle their runtime, styles, and graph JSON into one offline HTML file.

## Current behavior

- custom nodes for every AgentRoots record type
- lifecycle-driven styling
- complete text in a details panel
- evidence and relationship inspection
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
3. AgentRoots validates lifecycle, permissions, evidence rules, and optimistic concurrency.
4. AgentRoots appends an event and updates the current projection.
5. The viewer receives the new graph version.
6. Connected agents receive only an affected-record notification and refresh context when needed.

Candidate commands include `propose_revision`, `change_status`, `add_relationship`,
`remove_relationship`, `link_evidence`, and `resolve_contradiction`. The backend remains the sole
authority for every mutation.

`research_revise` now provides the first persistence primitive for that editor. It uses expected
revisions and append-only events. Revising accepted knowledge returns it to provisional status so
the changed meaning must be reviewed again.

For larger graphs, clustering and server-side graph scopes can be added without changing the
record or link contract. A separate WebGL overview may be introduced only if real projects exceed
React Flow's useful interactive scale.
