import React, { useCallback, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import "./viewer.css";
import dagre from "@dagrejs/dagre";

const TYPE_META = {
  goal: ["Goal", "◎"], question: ["Question", "?"], hypothesis: ["Hypothesis", "◇"],
  experiment: ["Experiment", "⚗"], run_ref: ["Run", "▶"], observation: ["Observation", "◉"],
  claim: ["Claim", "◆"], finding: ["Finding", "✦"], decision: ["Decision", "✓"],
  artifact_ref: ["Artifact", "▣"], evidence: ["Evidence", "⌁"], agent: ["Agent", "♙"],
  session: ["Session", "◷"],
};
const STATUS_ORDER = ["accepted", "provisional", "candidate", "disputed", "stale", "rejected", "superseded"];
const NODE_WIDTH = 258;
const NODE_HEIGHT = 112;

function KnowledgeNode({ data, selected }) {
  const [label, icon] = TYPE_META[data.type] || [data.type, "•"];
  return <article className={`knowledge-node status-${data.status} ${selected ? "is-selected" : ""}`}>
    <Handle type="target" position={Position.Left} />
    <div className="node-topline"><span className="node-icon">{icon}</span><span>{label}</span><span className="node-status">{data.status}</span></div>
    <div className="node-title">{data.title}</div>
    <div className="node-footer"><span>v{data.revision}</span><span>{data.evidence.length} evidence</span></div>
    <Handle type="source" position={Position.Right} />
  </article>;
}

function layoutGraph(records, links, direction) {
  const graph = new dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}));
  graph.setGraph({ rankdir: direction, ranksep: 110, nodesep: 48, edgesep: 24, marginx: 30, marginy: 30 });
  for (const record of records) graph.setNode(record.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  for (const link of links) graph.setEdge(link.source_id, link.target_id);
  dagre.layout(graph);
  const nodes = records.map(record => {
    const point = graph.node(record.id);
    return {
      id: record.id,
      type: "knowledge",
      position: { x: point.x - NODE_WIDTH / 2, y: point.y - NODE_HEIGHT / 2 },
      data: record,
    };
  });
  const edges = links.map((link, index) => ({
    id: `${link.source_id}:${link.relation}:${link.target_id}:${index}`,
    source: link.source_id,
    target: link.target_id,
    label: link.relation.replaceAll("_", " "),
    className: `relation-${link.relation}`,
    markerEnd: { type: MarkerType.ArrowClosed },
  }));
  return { nodes, edges };
}

function Toolbar({ data, query, setQuery, status, setStatus, type, setType, direction, setDirection, fit }) {
  const statuses = [...new Set(data.nodes.map(node => node.status))].sort((a, b) => STATUS_ORDER.indexOf(a) - STATUS_ORDER.indexOf(b));
  const types = [...new Set(data.nodes.map(node => node.type))].sort();
  return <header className="toolbar">
    <div className="brand"><span className="brand-mark">AR</span><div><strong>{data.project}</strong><small>Knowledge map v{data.graph_version}</small></div></div>
    <label className="search"><span>⌕</span><input value={query} onChange={event => setQuery(event.target.value)} placeholder="Search records and IDs" aria-label="Search records and IDs" /></label>
    <select value={status} onChange={event => setStatus(event.target.value)} aria-label="Filter lifecycle"><option value="">All lifecycles</option>{statuses.map(value => <option key={value}>{value}</option>)}</select>
    <select value={type} onChange={event => setType(event.target.value)} aria-label="Filter record type"><option value="">All record types</option>{types.map(value => <option key={value}>{value}</option>)}</select>
    <div className="segmented" aria-label="Layout direction"><button className={direction === "LR" ? "active" : ""} onClick={() => setDirection("LR")}>Wide</button><button className={direction === "TB" ? "active" : ""} onClick={() => setDirection("TB")}>Tall</button></div>
    <button className="fit-button" onClick={fit}>Fit map</button>
  </header>;
}

function DetailPanel({ record, links, recordsById, onClose }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => setCopied(false), [record?.id]);
  if (!record) return <aside className="detail empty-detail"><div className="empty-tree">⌁</div><h2>Select a record</h2><p>Inspect its complete text, evidence, lifecycle, version, and relationships.</p></aside>;
  const related = links.filter(link => link.source_id === record.id || link.target_id === record.id);
  const copy = async () => {
    try {
      if (!navigator.clipboard) throw new Error("clipboard API unavailable");
      await navigator.clipboard.writeText(record.id);
    } catch {
      const input = document.createElement("textarea");
      input.value = record.id; input.style.position = "fixed"; input.style.opacity = "0";
      document.body.appendChild(input); input.select(); document.execCommand("copy"); input.remove();
    }
    setCopied(true);
  };
  return <aside className="detail">
    <div className="detail-actions"><span className={`status-pill status-${record.status}`}>{record.status}</span><button onClick={onClose} aria-label="Close details">×</button></div>
    <div className="detail-type">{TYPE_META[record.type]?.[1] || "•"} {TYPE_META[record.type]?.[0] || record.type}</div>
    <h2>{record.title}</h2><p className="detail-body">{record.body}</p>
    <div className="id-block"><code>{record.id}</code><button onClick={copy}>{copied ? "Copied" : "Copy ID"}</button></div>
    <section><h3>Version</h3><dl><div><dt>Revision</dt><dd>{record.revision}</dd></div><div><dt>Creator</dt><dd>{record.creator}</dd></div><div><dt>Mode</dt><dd>{record.mode}</dd></div><div><dt>Updated</dt><dd>{new Date(record.updated_at).toLocaleString()}</dd></div></dl></section>
    <section><h3>Evidence <span>{record.evidence.length}</span></h3>{record.evidence.length ? record.evidence.map(item => <article className="evidence" key={item.id ?? item.uri}><strong>{item.kind}</strong><p>{item.summary || "Linked evidence"}</p><code>{item.uri}</code></article>) : <p className="muted">No evidence linked.</p>}</section>
    <section><h3>Relationships <span>{related.length}</span></h3>{related.length ? related.map((link, index) => { const outbound = link.source_id === record.id; const other = recordsById.get(outbound ? link.target_id : link.source_id); return <article className="relationship" key={`${link.relation}:${index}`}><span>{outbound ? "→" : "←"}</span><div><strong>{link.relation.replaceAll("_", " ")}</strong><p>{other?.title || "Unknown record"}</p><code>{other?.id}</code></div></article>; }) : <p className="muted">No relationships.</p>}</section>
    <footer>Editing command boundary ready for a future governed save API.</footer>
  </aside>;
}

function Viewer({ data }) {
  const flow = useReactFlow();
  const [query, setQuery] = useState(""); const [status, setStatus] = useState(""); const [type, setType] = useState(""); const [direction, setDirection] = useState("LR"); const [selectedId, setSelectedId] = useState(null);
  const recordsById = useMemo(() => new Map(data.nodes.map(node => [node.id, node])), [data]);
  const filtered = useMemo(() => { const needle = query.trim().toLowerCase(); return data.nodes.filter(node => (!needle || `${node.title} ${node.body} ${node.id}`.toLowerCase().includes(needle)) && (!status || node.status === status) && (!type || node.type === type)); }, [data, query, status, type]);
  const visibleIds = useMemo(() => new Set(filtered.map(node => node.id)), [filtered]);
  useEffect(() => { if (selectedId && !visibleIds.has(selectedId)) setSelectedId(null); }, [selectedId, visibleIds]);
  const visibleLinks = useMemo(() => data.edges.filter(edge => visibleIds.has(edge.source_id) && visibleIds.has(edge.target_id)), [data, visibleIds]);
  const model = useMemo(() => layoutGraph(filtered, visibleLinks, direction), [filtered, visibleLinks, direction]);
  const fit = useCallback(() => requestAnimationFrame(() => flow.fitView({ padding: 0.18, duration: 350 })), [flow]);
  useEffect(() => { fit(); }, [model, fit]);
  const connected = useMemo(() => { if (!selectedId) return null; const ids = new Set([selectedId]); for (const edge of data.edges) if (edge.source_id === selectedId) ids.add(edge.target_id); else if (edge.target_id === selectedId) ids.add(edge.source_id); return ids; }, [data, selectedId]);
  const styledNodes = model.nodes.map(node => ({ ...node, className: connected && !connected.has(node.id) ? "is-dimmed" : "" }));
  const styledEdges = model.edges.map(edge => ({ ...edge, className: `${edge.className} ${connected && edge.source !== selectedId && edge.target !== selectedId ? "is-dimmed" : ""}` }));
  return <div className="app-shell">
    <Toolbar {...{ data, query, setQuery, status, setStatus, type, setType, direction, setDirection, fit }} />
    <main className="viewer-grid"><section className="canvas"><ReactFlow nodes={styledNodes} edges={styledEdges} nodeTypes={{ knowledge: KnowledgeNode }} nodesDraggable={false} nodesConnectable={false} elementsSelectable onNodeClick={(_, node) => setSelectedId(node.id)} onPaneClick={() => setSelectedId(null)} fitView minZoom={0.15} maxZoom={2.5} proOptions={{ hideAttribution: true }}><Background variant={BackgroundVariant.Dots} gap={22} size={1} /><Controls showInteractive={false} /></ReactFlow><div className="map-count">{filtered.length} records · {visibleLinks.length} relationships</div></section><DetailPanel record={recordsById.get(selectedId)} links={data.edges} recordsById={recordsById} onClose={() => setSelectedId(null)} /></main>
  </div>;
}

const data = JSON.parse(document.getElementById("graph-data").textContent);
createRoot(document.getElementById("agentroots-viewer")).render(<ReactFlowProvider><Viewer data={data} /></ReactFlowProvider>);
