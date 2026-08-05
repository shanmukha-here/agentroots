from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any


def _asset(name: str) -> str:
    return (Path(__file__).with_name("assets") / name).read_text(encoding="utf-8")


def write_graph_html(graph: dict[str, Any], destination: Path) -> Path:
    """Write a self-contained, read-only interactive project map."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(graph, ensure_ascii=False).replace("</", "<\\/")
    document = (
        _DOCUMENT.replace("__PROJECT__", escape(str(graph["project"])))
        .replace("__GRAPH_DATA__", payload)
        .replace("__VIEWER_CSS__", _asset("graph-viewer.css"))
        .replace("__VIEWER_JS__", _asset("graph-viewer.js"))
    )
    destination.write_text(document, encoding="utf-8")
    return destination


_DOCUMENT = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="generator" content="AgentRoots">
<title>__PROJECT__ | AgentRoots knowledge map</title>
<style>__VIEWER_CSS__</style>
</head>
<body>
<div id="agentroots-viewer"></div>
<script id="graph-data" type="application/json">__GRAPH_DATA__</script>
<script>__VIEWER_JS__</script>
</body>
</html>
"""
