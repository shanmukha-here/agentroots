import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

FIXTURE_AT = datetime(2026, 1, 1, tzinfo=UTC).isoformat()


def proposed_event(
    *, project: str, key: str, record_type: str, title: str, body: str, mode: str
) -> dict[str, object]:
    record_id = str(uuid5(NAMESPACE_URL, f"agentroots:fixture:record:{project}:{key}"))
    event_id = str(uuid5(NAMESPACE_URL, f"agentroots:fixture:event:{project}:{key}"))
    record = {
        "id": record_id,
        "project": project,
        "type": record_type,
        "title": title,
        "body": body,
        "creator": "fixture-generator",
        "mode": mode,
        "status": "candidate",
        "revision": 1,
        "metadata": {"fixture_key": key, "synthetic": True},
        "created_at": FIXTURE_AT,
        "updated_at": FIXTURE_AT,
    }
    return {
        "event_id": event_id,
        "project": project,
        "record_id": record_id,
        "revision": 1,
        "event_type": "proposed",
        "actor": "fixture-generator",
        "at": FIXTURE_AT,
        "payload": record,
        "idempotency_key": None,
    }


def main() -> None:
    destination = Path(__file__).with_name("synthetic_1000_experiments.jsonl")
    with destination.open("w", encoding="utf-8") as stream:
        for index in range(1000):
            event = proposed_event(
                project="synthetic-scale",
                key=f"experiment-{index:04d}",
                record_type="experiment",
                title=f"Synthetic experiment {index:04d}",
                body=(
                    "This controlled synthetic experiment exercises protocol import and retrieval. "
                    f"Its external run remains at mlflow://runs/synthetic-{index:04d}. "
                    f"The selected synthetic score is {index / 1000:.3f}. "
                    "No command or artifact is embedded in this fixture."
                ),
                mode=["preregistered", "exploratory", "replication", "debugging"][index % 4],
            )
            stream.write(json.dumps(event, separators=(",", ":")) + "\n")

    small = Path(__file__).with_name("synthetic_fixture.jsonl")
    examples = [
        proposed_event(
            project="cache-study",
            key="cache-hypothesis",
            record_type="hypothesis",
            title="Cache lowers latency",
            body=(
                "Repeated reads may benefit from a bounded cache. "
                "The hypothesis predicts lower median latency under a fixed workload. "
                "Memory use must remain within the configured budget. "
                "A controlled experiment should compare cold and warm paths."
            ),
            mode="preregistered",
        ),
        proposed_event(
            project="cache-study",
            key="large-cache-observation",
            record_type="observation",
            title="Large cache failed",
            body=(
                "The oversized cache increased memory pressure in the synthetic trial. "
                "Its latency gain disappeared after paging began. "
                "This remains an unreviewed candidate until evidence is linked. "
                "Future trials should use a smaller bounded cache."
            ),
            mode="exploratory",
        ),
    ]
    small.write_text(
        "".join(json.dumps(event, separators=(",", ":")) + "\n" for event in examples),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
