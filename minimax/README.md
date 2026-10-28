# MiniMax SDD Context

This project provides a CLI that imports real project material into seekdb, retrieves it to build task context, calls MiniMax to produce a unified diff, and persists task handoff state in PowerContext. seekdb uses the official `pylibseekdb` local runtime.

## Configuration

Copy `.env.example` to `.env` and set `MINIMAX_API_KEY` locally. The repository root `.gitignore` excludes `.env`. For Token Plan keys, use the OpenAI-compatible coding endpoint and model shown in the MiniMax Token Plan console; set `MINIMAX_BASE_URL` and `MINIMAX_MODEL` there if your account displays different values. Never commit credentials.

## Start services

Requires Python 3.11+ and `uv`.

```bash
cd minimax
uv sync
```

`pylibseekdb` starts the official seekdb 1.4 local runtime in `SEEKDB_DIR`; imported docs persist there. Run PowerContext using a local data directory:

```bash
mkdir -p powercontext-data
POWERCONTEXT_HOME=$PWD/powercontext-data uv run powercontext server run --port 8010
```

## Validation flow

```bash
uv run python -m sdd_context.cli import ../qoder/topic-desk
uv run python -m sdd_context.cli context "topic_detail optional kind filter"
uv run python -m sdd_context.cli generate "Add optional kind note filter"
uv run pytest
uv run python -m sdd_context.cli state "Add optional kind note filter" --done "context retrieved" --todo "review and apply generated patch" --tests "not run"
```

Review generated diff before applying it. Run the fixed target tests before and after the MiniMax change and preserve both logs. Restart the context CLI/PowerContext client and use `POWERCONTEXT_SCOPE_ID=<printed scope_id> uv run sdd-context context ...` to include stored handoff state. Re-import after changing a sample specification; retrieved excerpts come from seekdb rows and reflect updated content.

## Known validation status

See `evidence/` for the MiniMax change record and target-test results. Credentials and private reasoning are not stored.
