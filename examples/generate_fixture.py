import json
from pathlib import Path


def main() -> None:
    destination = Path(__file__).with_name("synthetic_1000_experiments.jsonl")
    with destination.open("w", encoding="utf-8") as stream:
        for index in range(1000):
            record = {
                "id": f"experiment-{index:04d}",
                "project": "synthetic-scale",
                "kind": "E",
                "title": f"Synthetic experiment {index:04d}",
                "summary": "Controlled synthetic result for retrieval and scale tests.",
                "mode": ["preregistered", "exploratory", "replication", "debugging"][index % 4],
                "run": {"uri": f"mlflow://runs/synthetic-{index:04d}", "score": index / 1000},
            }
            stream.write(json.dumps(record, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
