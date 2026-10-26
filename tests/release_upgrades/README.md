# Installed-release upgrade acceptance

`release/upgrade-support.json` requires **positive** 1.3.0 upgrades on SQLite, embedded seekDB, and OceanBase.
The current `upgrade-required` mode keeps the upgrade support declaration unverified while requiring every case
to run and pass. Missing full migration integration (#2154) fails the gate. `supported-upgrades` runs the same
matrix and enables publication after the reviewed support declaration is updated. `fresh-install-only` is rejected.

## Fixed source and current target

The `automatic_sources` policy uses `strategy: "fixed-version"` and `source_version: "1.3.0"`, with SQLite,
embedded seekDB, and OceanBase profiles. Every run expands that exact source across the three backends. The target
is the **actual wheel built by this job**, not the latest published PyPI package. Thus releases 1.4.1 and 1.5.0
exercise 1.3.0 → 1.4.1 and 1.3.0 → 1.5.0 without adding test scripts or changing the source version. Newly published
intermediate versions do not become additional sources. This fixed baseline does not establish support for every
intermediate release's database or data shapes; add explicit `cases` if those need separate acceptance.

The runner resolves the exact source wheel from public PyPI and installs its URL with a SHA-256. Missing/yanked
source wheels, invalid artifact metadata, catalog outages, and a target at or below the source version fail the gate.
Evidence records the catalog digest, exact source artifact, target artifact digest, and expanded matrix. The resolver
requires universal `py3-none-any` wheels; a distribution format change must extend it explicitly.

All three profiles use `test_full_server.py`, which accepts only `full-server-no-vector`. Any other configuration
fails before opening a database; adding a policy entry alone does not implement a new profile. Backend evidence
records the executed configuration and the controller requires it to match the declaration.

The suite starts the installed 1.3.0 Server, writes data through HTTP,
stops it, seeds a pending processing intent using the installed SDK, and runs the target wheel's public
`server db-migrate status/plan/apply/verify` commands. Verification must report `state=ready` and `server_ready=true`.
The target Server must then start, preserve data and permissions, accept new writes, and support retrieval.
Partial migration success, missing framework/baseline support, and startup rejection all fail. These disposable
fixtures explicitly accept no backup; this is not a production upgrade procedure.

`cases` remains available for additional exact source versions/configurations. Each case needs `source_version`,
`backend`, `configuration`, and `suite`; overlapping cases fail. Optional features that create persistent objects
require additional profiles. The fixed CI baseline and advertised upgrade support must stay consistent.

PR/master and publication CI start an isolated OceanBase CE container using the image and MySQL tenant setup from
the existing E2E workflow. They wait for SQL readiness and adequate datafile capacity before testing, save diagnostics,
and remove the container even when a check fails. No external database credentials are needed. Embedded seekDB
runs directly in a temporary runner directory. A backend startup failure fails the job instead of skipping it.

The runner creates separate source/target environments, installs the exact published source package and the built
target wheel, verifies their versions, then executes the reviewed suite in an isolated working directory. Repository
pytest configuration and deployment environment variables are excluded. The suite must exercise those installed
processes, not import the current checkout's runtime in place of either version. Use `-I` when invoking their Python.

The suite receives:

- `PC_UPGRADE_SOURCE_PYTHON`, `PC_UPGRADE_TARGET_PYTHON`: isolated installed interpreters.
- `PC_UPGRADE_SOURCE_VERSION`: the exact source release selected for this case.
- `PC_UPGRADE_BACKEND`, `PC_UPGRADE_CONFIGURATION`: the declared case.
- `PC_UPGRADE_WORK`: disposable workspace for both databases, controlled inference fixtures, and environment files.
- `PC_UPGRADE_EVIDENCE`: destination for backend evidence JSON.
- `PC_UPGRADE_REPORT_DIR`: persistent per-case command logs, fixture snapshots and schema inventories.
- `PC_UPGRADE_OCEANBASE_URL`, `PC_UPGRADE_OCEANBASE_FRESH_URL`: dedicated disposable MySQL-tenant databases for
  the upgraded and fresh-install comparisons. CI supplies both; local positive OceanBase suites must supply their
  own isolated databases. Each suite must isolate/reset its own fixtures when multiple configurations run.

Provide these behavioral tests (additional tests are allowed):

| Test | Required behavior |
| --- | --- |
| `test_source_release_database` | Start the actual source Server to create its complete database. Populate representative Memory history, references, permissions, Candidates, tasks and cursors through that version's public interfaces; verify the data before shutdown. Do not create a minimal schema by hand. |
| `test_public_migration_commands` | Stop writers, run the target artifact's public maintenance commands, and validate full readiness. A limited bundle verification does not pass. |
| `test_target_startup` | Start the target Server against the migrated database and verify readiness. |
| `test_read_write_and_retrieval` | Read the old records, write new ones, and retrieve both through public interfaces. |
| `test_authorization` | Preserve the old viewer grant; verify its allowed reads, denied cross-scope reads and denied writes with both installed releases. Invalid credentials must still fail. |
| `test_candidates_and_processing` | Verify retained Candidates and CAS approval; load and acknowledge the old pending intent using the target installed SDK, including idempotency. This does not execute a model-driven Worker. |
| `test_preserved_history_and_cursors` | Verify exact history/references and unchanged or declared-transformed cursor and receipt semantics. |
| `test_fresh_upgrade_equivalence` | Initialize a fresh target database; compare schema/backend objects and behavior with the upgraded database. The current suite has no retained/archive object exclusions. |

The authorization probe runs the installed Server through its native SDK and real HTTP handlers with a fixed
ordinary-user authentication provider. Authorization uses the persisted builtin policy without overrides. The CLI
Server is stopped before each SDK probe so embedded seekDB has only one owning process. CLI startup remains a
separate mandatory acceptance phase.

Use a session fixture for the shared lifecycle; do not depend on pytest's test execution order. All collected tests
must execute and pass. Empty collection, missing phases, skips (including missing backends), xfail, failure, command
failure, and missing/malformed reports fail the gate. A real OceanBase suite must provision an isolated database and
fail if its required environment is unavailable; it cannot replace execution with mocks. Controlled model responses
may test domain behavior, but do not establish model quality.

Write backend evidence containing `backend`, nonempty `engine_version`, `driver_version`, and recorded schema
inventories or their digests in `source_schema`, `target_schema`, and `fresh_schema`. OceanBase additionally requires
`tenant_mode: "mysql"` and `proxy_version` (or `direct`). Do not record credentials or business payloads. The controller
records installed Python/package versions, target wheel SHA-256, policy SHA-256, and the individual case result.

Evidence is created during the gate run in a new directory. There is no option to import old successful reports.
Test names and metadata do not themselves prove correctness: reviewers must inspect the assertions and confirm
they follow actual installed operations and compare persisted data. The profile exercises explicit writes and FTS,
not vector storage or model-generated content. The processing fixture tests persisted intent compatibility and
acknowledgment, not Worker execution. Add separately reviewed profiles before claiming those optional paths.
SQLite inventories include indexes, views and triggers; MySQL-family table DDL includes indexes and constraints,
and views are collected separately. Comparison tokenizes DDL while preserving quoted tokens and comments verbatim,
and ignores only unquoted whitespace and table-level auto-increment counters; structural differences fail rather than being silently excluded.

Regenerate installation support regions with
`uv run python scripts/upgrade_release_gate.py --write-docs` and run `make upgrade-policy-check`. When persistence
inputs change, calculate `managed_digest(ROOT)` from `upgrade_release_gate`, review the compatibility impact, and
update both the digest and rationale in `schema_review`. The inputs include persistence, optional code storage,
Server identity and authorization, and shared limits used in table definitions. Add new schema owners to
`MANAGED_INPUTS` when introducing them. The digest intentionally includes non-schema code: it forces a review of
changes, but does not infer semantic compatibility or replace real upgrade tests.

Build one wheel and its matching `.tar.gz` source distribution into `dist/`, then run `make upgrade-release-test`. Before release, additionally run
`uv run python scripts/run_upgrade_release_gate.py --release --release-notes /path/to/notes.md`. The notes must contain
the exact English support declaration generated in the installation guide. The release workflow performs this check
against the GitHub event's notes and gates both PyPI publication and release assets on the same tested wheel.

Repository administrators must require **Upgrade release gate** in branch protection. Workflow code cannot configure
that setting. Because the release workflow starts on `release.published`, it can block package/asset publication,
not the earlier manual creation of a GitHub Release; run the prepublication command before publishing that page.

## Historical rejection regressions

`scripts/ci_legacy_upgrade_rejection.py` remains a diagnostic tool for historical rejection behavior. PR and release
workflows do not invoke it as upgrade acceptance. An expected rejection or its `--respect-policy` result can never
replace a successful positive upgrade. The gate attempts every required backend even after an earlier case fails,
then fails overall and archives each case's logs and result. Empty matrices and missing suites are errors.

## Publication artifacts

The runner validates every distribution input. Publication requires exactly one wheel and one source archive with
matching names and metadata. It rebuilds the source archive with `uv build --wheel` and compares wheel member bytes,
excluding only the derived `RECORD` file. A same-version stale source archive fails just like an older-version one.
PRs may test a wheel alone, but the PR workflow builds both formats to exercise this validation too.

Acceptance uses a private copy of those files. Only a passing run creates `validated-dist/` and `publication.json`
in its evidence directory. CI uploads that directory; local `make publish` calls the runner with `--release --publish`
and uploads its exact returned paths, never a fresh glob of `dist/`. Failed acceptance produces no publication set.
Managed persistence hashes normalize CRLF/LF checkout line endings; artifact SHA-256s remain byte-exact.

## Process ownership

Full installed-release acceptance runs on Linux, including local invocations of the complete gate.
Other hosts can run policy, publication and schema regression checks, but cannot run full acceptance.
Each installation or acceptance command runs under an isolated Linux child subreaper. It retains ownership
of detached Server and embedded-engine descendants, allows graceful shutdown, then forces termination
and reaps remaining processes before workspace deletion or the next backend. Timeout, failure, success
with leaked children, and gate cancellation all use this cleanup. A parent-death signal starts cleanup
if the gate itself exits. An uninterruptible kernel wait blocks cleanup rather than admitting another case
while the previous processes still own storage.
