# Contributing

Open issue before broad ontology or backend changes. Keep core small and adapter boundaries strict.
Never add transcripts, secrets, generated DBs, external artifacts, or command execution.

```bash
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check .
python -m mypy src
python -m build
```

Changes to JSON contracts require schema, migration, round-trip test, and compatibility note.
Security fixes should follow `SECURITY.md` rather than public proof-of-concept disclosure.

