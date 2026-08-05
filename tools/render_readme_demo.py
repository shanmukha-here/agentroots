from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentroots.db import Database
from agentroots.graph import write_graph_html
from agentroots.models import EvidenceLink
from agentroots.service import ResearchService

WIDTH, HEIGHT, FPS, DURATION = 960, 540, 30, 11
INK = "#17251d"
GREEN = "#1f5d3a"
NAVY = "#243b6b"
CREAM = "#f4f0e3"
PAPER = "#ebe8d9"
MUTED = "#657268"
RED = "#a74834"
GOLD = "#d49a25"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        Path("C:/Windows/Fonts/seguisb.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def rounded(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], radius: int, fill: str, outline: str | None = None, width: int = 1) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def fit_text(draw: ImageDraw.ImageDraw, text: str, max_width: int, start: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    size = start
    while size > 10:
        candidate = font(size, bold)
        if draw.textbbox((0, 0), text, font=candidate)[2] <= max_width:
            return candidate
        size -= 1
    return font(10, bold)


def build_state(tmp: Path) -> dict[str, Any]:
    service = ResearchService(Database(tmp / "demo.sqlite3"))
    project = "agentroots-demo"

    origin = service.propose(
        project=project,
        type="origin",
        title="Agents should share grounded project state",
        body="Repeated repository reads and disposable agent findings motivated a durable cross-agent state layer.",
        creator="codex-main",
        mode="exploratory",
    )
    service.review(origin["id"], actor="codex-reviewer", verdict="provisional")
    service.link_evidence(
        EvidenceLink(origin["id"], "git://agentroots/README.md", "git-file", "Project thesis and scope", hashlib.sha256(b"agentroots-thesis").hexdigest()),
        actor="codex-reviewer",
    )
    origin = service.review(origin["id"], actor="codex-reviewer", verdict="accepted")

    goal = service.propose(
        project=project,
        type="goal",
        title="Resume work without rebuilding context",
        body="A fresh agent should recover the current frontier from a compact reviewed packet.",
        creator="codex-main",
    )
    service.link(goal["id"], origin["id"], "depends_on", "codex-main")

    failed = service.propose(
        project=project,
        type="observation",
        title="Full transcript replay wastes the context budget",
        body="A worker replayed the complete session and consumed most of the available context without improving task accuracy.",
        creator="deepseek-worker",
        mode="exploratory",
        metadata={"failed": True, "do_not_repeat": True},
    )
    service.review(failed["id"], actor="codex-reviewer", verdict="provisional")
    service.link_evidence(
        EvidenceLink(
            failed["id"],
            "mlflow://runs/failed-replay-017",
            "mlflow-run",
            "Failed run: 6,842 context tokens, no task gain",
            metadata={"run_id": "failed-replay-017", "external_status": "FAILED"},
        ),
        actor="deepseek-worker",
    )
    failed = service.review(failed["id"], actor="codex-reviewer", verdict="accepted")
    service.link(failed["id"], goal["id"], "tests", "deepseek-worker")

    finding = service.propose(
        project=project,
        type="finding",
        title="Reviewed packets prevent repeated repository reads",
        body="The fresh Codex agent resumed from accepted findings and the failed-run warning instead of rereading the same project files.",
        creator="codex-subagent",
        mode="replication",
    )
    service.review(finding["id"], actor="codex-reviewer", verdict="provisional")
    service.link_evidence(
        EvidenceLink(
            finding["id"],
            "test://shared-state/workflow-004",
            "test-run",
            "Workflow completed with one repository read pass",
            hashlib.sha256(b"workflow-004-pass").hexdigest(),
        ),
        actor="codex-reviewer",
    )
    finding = service.review(finding["id"], actor="codex-reviewer", verdict="accepted")
    service.link(finding["id"], goal["id"], "supports", "codex-reviewer")
    service.link(finding["id"], failed["id"], "derived_from", "codex-reviewer")

    caveat = service.propose(
        project=project,
        type="question",
        title="How should stale code findings be refreshed?",
        body="Git evidence can invalidate code-dependent claims, but the replacement review remains future work.",
        creator="codex-main",
    )
    service.link(caveat["id"], finding["id"], "depends_on", "codex-main")

    packet = service.context(project, token_budget=1400)
    graph = service.graph(project)
    graph_html = write_graph_html(graph, tmp / "agentroots-knowledge-map.html")
    return {
        "project": project,
        "origin": origin,
        "goal": goal,
        "failed": failed,
        "finding": finding,
        "caveat": caveat,
        "packet": packet,
        "graph": graph,
        "graph_html": graph_html,
    }


def find_browser() -> Path | None:
    candidates = [
        Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
        Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
    ]
    for command in ("msedge", "chromium", "chromium-browser", "google-chrome"):
        found = shutil.which(command)
        if found:
            candidates.append(Path(found))
    return next((path for path in candidates if path.exists()), None)


def capture_graph(html: Path, output: Path) -> None:
    browser = find_browser()
    if browser is None:
        raise RuntimeError("Edge or Chromium is required to capture the real graph viewer")
    subprocess.run(
        [
            str(browser),
            "--headless",
            "--disable-gpu",
            "--hide-scrollbars",
            "--window-size=1280,720",
            f"--screenshot={output}",
            html.resolve().as_uri(),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def base_frame(logo: Image.Image) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (WIDTH, HEIGHT), PAPER)
    draw = ImageDraw.Draw(image)
    for x in range(10, WIDTH, 28):
        for y in range(10, HEIGHT, 28):
            draw.ellipse((x, y, x + 1, y + 1), fill="#d2d4c7")
    image.paste(logo, (22, 18), logo)
    draw.text((71, 21), "AgentRoots", font=font(17, True), fill=INK)
    draw.text((71, 42), "Different agents. Same roots.", font=font(9), fill=MUTED)
    rounded(draw, (817, 20, 936, 45), 12, "#e4eadf", GREEN)
    draw.text((838, 27), "REAL STATE", font=font(9, True), fill=GREEN)
    return image, draw


def title(draw: ImageDraw.ImageDraw, heading: str, subheading: str) -> None:
    draw.text((30, 83), heading, font=fit_text(draw, heading, 900, 31, True), fill=INK)
    draw.text((31, 124), subheading, font=font(15), fill=MUTED)


def terminal(draw: ImageDraw.ImageDraw, lines: list[tuple[str, str]], y: int = 164) -> None:
    rounded(draw, (30, y, 930, 500), 14, "#15251b", "#2d4b39", 2)
    draw.ellipse((48, y + 18, 56, y + 26), fill="#e56c60")
    draw.ellipse((62, y + 18, 70, y + 26), fill="#e9bd4e")
    draw.ellipse((76, y + 18, 84, y + 26), fill="#65b36f")
    cursor = y + 48
    for text, color in lines:
        draw.text((50, cursor), text, font=font(14), fill=color)
        cursor += 29


def scene(frame: int, state: dict[str, Any], logo: Image.Image, graph: Image.Image) -> Image.Image:
    t = frame / FPS
    image, draw = base_frame(logo)
    if t < 1.45:
        title(draw, "Agents forget.", "Their useful work should not.")
        draw.text((31, 190), "Governed project state for every agent that comes next.", font=font(20), fill=GREEN)
        rounded(draw, (31, 245, 245, 280), 17, GREEN)
        draw.text((54, 254), "AGENT CONTINUITY MCP", font=font(12, True), fill=CREAM)
        image.paste(logo.resize((190, 190), Image.Resampling.LANCZOS), (705, 285), logo.resize((190, 190), Image.Resampling.LANCZOS))
    elif t < 3.0:
        title(draw, "Without shared state", "Three agents reconstruct the same context.")
        progress = min(1.0, (t - 1.45) / 1.1)
        rows = [("MAIN", NAVY), ("WORKER A", "#bf762d"), ("WORKER B", "#765098")]
        for index, (label, color) in enumerate(rows):
            y = 190 + index * 82
            rounded(draw, (34, y, 142, y + 42), 9, color)
            draw.text((55, y + 12), label, font=font(11, True), fill="white")
            rounded(draw, (162, y, 894, y + 42), 21, "#d7dbd0")
            rounded(draw, (162, y, 162 + int(732 * progress), y + 42), 21, "#9ca99b")
            draw.text((180, y + 13), "reading the same repository files...", font=font(12), fill="#405147")
        draw.text((758, 447), "3x repeated reads", font=font(14, True), fill=RED)
    elif t < 4.65:
        title(draw, "Agent 1 grows the roots", "Real records created through ResearchService.")
        terminal(
            draw,
            [
                ("$ agentroots propose origin \"Shared grounded state\"", "#dbe8dc"),
                (f"created  {state['origin']['id'][:8]}  status={state['origin']['status']}", "#75c88a"),
                ("$ agentroots propose goal \"Resume without rereading\"", "#dbe8dc"),
                (f"created  {state['goal']['id'][:8]}  status={state['goal']['status']}", "#75c88a"),
                ("$ agentroots link goal depends_on origin", "#dbe8dc"),
                ("linked  durable project origin -> active goal", "#e3bd5d"),
            ],
        )
    elif t < 6.35:
        title(draw, "Agent 2 preserves the dead end", "Negative results become durable instructions.")
        terminal(
            draw,
            [
                ("$ agentroots propose observation \"Transcript replay wastes context\"", "#dbe8dc"),
                (f"created  {state['failed']['id'][:8]}  status=provisional", "#e3bd5d"),
                ("$ research_link_evidence mlflow://runs/failed-replay-017", "#dbe8dc"),
                ("evidence  FAILED  6,842 tokens  no task gain", "#e59a80"),
                ("$ research_review accepted --actor codex-reviewer", "#dbe8dc"),
                (f"accepted {state['failed']['id'][:8]}  DO NOT REPEAT", "#75c88a"),
            ],
        )
    elif t < 8.15:
        title(draw, "Fresh agent. No transcript.", "The real 1,400-token packet returns only the grounded frontier.")
        rounded(draw, (31, 164, 929, 495), 14, "#f7f3e8", "#cbd1c4", 2)
        draw.text((54, 184), "research_get_context", font=font(12, True), fill=GREEN)
        draw.text((791, 184), f"{state['packet']['estimated_tokens']:,} tokens", font=font(12, True), fill=MUTED)
        sections = state["packet"]["sections"]
        records = (
            sections["project_origin"]
            + sections["current_goal"]
            + sections["accepted_findings"]
            + sections["active_questions_hypotheses"]
        )[:4]
        y = 225
        colors = {"accepted": GREEN, "provisional": GOLD, "candidate": NAVY}
        for record in records:
            draw.rounded_rectangle((54, y, 61, y + 50), radius=3, fill=colors.get(record["status"], MUTED))
            draw.text((76, y), f"{record['type'].upper()}  |  {record['status'].upper()}", font=font(10, True), fill=colors.get(record["status"], MUTED))
            draw.text((76, y + 19), record["title"], font=fit_text(draw, record["title"], 800, 15, True), fill=INK)
            y += 61
        rounded(draw, (760, 452, 904, 478), 13, GREEN)
        draw.text((779, 459), "READY TO CONTINUE", font=font(9, True), fill="white")
    else:
        title(draw, "One state for agents and humans", "Actual graph viewer generated from the same event ledger.")
        panel = graph.copy()
        panel.thumbnail((900, 345), Image.Resampling.LANCZOS)
        rounded(draw, (29, 157, 931, 508), 14, "#f7f3e8", "#cbd1c4", 2)
        image.paste(panel, (30 + (900 - panel.width) // 2, 160 + (345 - panel.height) // 2))
    return image


def render(output: Path) -> None:
    try:
        import imageio_ffmpeg
    except ImportError as exc:
        raise SystemExit("Install the demo extra: python -m pip install -e .[demo]") from exc
    output.mkdir(parents=True, exist_ok=True)
    logo = Image.open(ROOT / "docs/assets/brand/agentroots-mark-512.png").convert("RGBA")
    logo.thumbnail((42, 42), Image.Resampling.LANCZOS)
    with TemporaryDirectory() as directory:
        tmp = Path(directory)
        state = build_state(tmp)
        graph_path = tmp / "graph-viewer.png"
        capture_graph(state["graph_html"], graph_path)
        graph = Image.open(graph_path).convert("RGB")
        frames = [scene(index, state, logo, graph) for index in range(FPS * DURATION)]

    webp = output / "agentroots-readme-demo-v3.webp"
    frames[0].save(
        webp,
        save_all=True,
        append_images=frames[1:],
        duration=1000 // FPS,
        loop=0,
        quality=80,
        method=6,
    )
    mp4 = output / "agentroots-readme-demo-v3.mp4"
    writer = imageio_ffmpeg.write_frames(
        str(mp4),
        (WIDTH, HEIGHT),
        fps=FPS,
        codec="libx264",
        pix_fmt_in="rgb24",
        pix_fmt_out="yuv420p",
        quality=7,
        macro_block_size=2,
        ffmpeg_log_level="error",
    )
    writer.send(None)
    try:
        for frame_image in frames:
            writer.send(frame_image.tobytes())
    finally:
        writer.close()
    print(f"Rendered {len(frames)} authentic frames")
    print(webp)
    print(mp4)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render the reproducible AgentRoots README demo")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs")
    args = parser.parse_args()
    render(args.output.resolve())


if __name__ == "__main__":
    main()
