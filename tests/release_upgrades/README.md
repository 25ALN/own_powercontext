# Installed-release upgrade acceptance

`release/upgrade-support.json` requires **positive** 1.3.0 upgrades on SQLite, embedded seekDB, and OceanBase.
The current `upgrade-required` mode keeps the upgrade support declaration unverified while requiring every case
to run and pass. Missing full migration integration (#2154) fails the gate. `supported-upgrades` runs the same
matrix and enables publication after the reviewed support declaration is updated. `fresh-install-only` is rejected.

## Reviewed sources and current target

The declared target release is **1.4.1**. Public migration output maps the two source baselines to
`v1_3_0_000` / `v1_4_0_000` and the shared steps to `v1_4_1_001`–`v1_4_1_003`. The suite checks
these names while retaining the original revision IDs in execution and recovery evidence.

The `automatic_sources` policy uses `strategy: "fixed-version"` and `source_version: "1.3.0"`.
Explicit `cases` are empty. CI requires twelve configurations of the 1.3.0 source across SQLite,
embedded seekDB, and OceanBase. The target is the **actual wheel built by this job**, not the latest published
PyPI package. Later target releases keep the same 1.3.0 baseline; intermediate releases are not added automatically.
The framework and fixture retain the independently registered 1.4.0 adapter, but CI does not execute its
installed-release upgrade path. Passing this gate does not establish 1.4.0 upgrade acceptance. This matrix does not establish support for every intermediate release's database or
data shapes; add reviewed explicit `cases` and source adapters if those need separate acceptance.

The runner resolves the exact source wheel from public PyPI and installs its URL with a SHA-256. Missing/yanked
source wheels, invalid artifact metadata, catalog outages, and a target at or below the source version fail the gate.
Evidence records the catalog digest, exact source artifact, target artifact digest, and expanded matrix. The resolver
requires universal `py3-none-any` wheels; a distribution format change must extend it explicitly.

The selected source uses all twelve profiles in `test_full_server.py`: Code enabled or disabled, with or without vectors,
on SQLite, seekDB and OceanBase. Vector cases use exact unit-normalized L2 profiles: dimension three without Code and
dimension four with Code. The local controlled embedding endpoint serves actual installed source/target processes;
it does not replace either runtime or its persistence. Each source Server writes nonempty Atomic Memory and Topic
vectors. Maintenance must preserve the exact historical embedding digests without calling that endpoint. Target
acceptance checks historical and new vector retrieval, Topic content, configured profile drift without writes,
and fresh-storage equivalence. SQLite additionally rejects a missing vector helper without repairing the clone.
These assertions establish deterministic execution and space preservation, not embedding model quality.
Code cases preserve immutable cache files and original fingerprint/symbol identities. Embedded seekDB additionally
preserves the exact graph-table row digests through maintenance; its frozen extension creates the same schema for fresh storage.
The CI suite starts the installed 1.3.0 Server, writes data through HTTP,
stops it, seeds a pending processing intent using the installed SDK, and runs the target wheel's public
`server db-migrate status/plan/apply/verify` commands. Reviewed adapters select the source Candidate API and table
names. The retained, unselected 1.4.0 adapter creates a pending intent with `consecutive_dream_attempts=1`; target readback must preserve
every field supplied by the source SDK before idempotent acknowledgment. The 1.3.0 fixture retains its original
pending-intent contract. Verification must report `state=ready` and `server_ready=true`.
The target Server must then start, preserve data and permissions, accept new writes, and support retrieval.
Partial migration success, missing framework/baseline support, and startup rejection all fail. SQLite profiles use
automatic backup and restore that recovery point into a separate database. The suite compares the complete
restored source schema and row digests, reads historical Memory, Candidates, grants and cursors with the installed
source release, upgrades the restored database again, then verifies target reads, FTS and new writes. Code-enabled
SQLite also preserves its separate immutable cache files. The original backup checksum must remain unchanged.
seekDB and OceanBase fixtures explicitly accept no backup; these cases do not establish backup restoration.
This is not a production upgrade procedure.

`cases` remains available for additional exact source versions/configurations. Each case needs `source_version`,
`backend`, `configuration`, and `suite`; overlapping cases fail. Optional features that create persistent objects
require additional profiles. The fixed CI baseline and advertised upgrade support must stay consistent.

PR/master and publication CI start an isolated OceanBase CE container using the image and MySQL tenant setup from
the existing E2E workflow. They wait for SQL readiness and adequate datafile capacity before testing, save diagnostics,
and remove the container even when a check fails. No external database credentials are needed. Embedded seekDB
runs directly in a temporary runner directory. A backend startup failure fails the job instead of skipping it.
OceanBase reserves separate upgraded and fresh databases for every source/configuration pair. The default
connection URLs remain `pc_upgrade_test` and `pc_upgrade_fresh`; the suite selects the corresponding configuration
and, for 1.4.0, the `pc_upgrade_140` prefix before either Server is started. Every selected database must be empty.
The PR and publication jobs allow six hours for the twelve cases and environment setup. The thirty-minute
per-case deadline and descendant cleanup are unchanged; increasing the overall budget does not turn a timeout
into successful acceptance.

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
| `test_legacy_startup_is_rejected` | Target startup on the old database must fail without changing schema or row digests. |
| `test_repeated_migration` | A second plan/apply/verify returns unchanged and preserves every table row digest. |
| `test_public_migration_commands` | Stop writers, run the target artifact's public maintenance commands, and validate full readiness. SQLite additionally restores the actual automatic recovery point, reads it with the source Server, upgrades it and verifies target use. A limited bundle verification does not pass. |
| `test_target_startup` | Start the target Server against the migrated database and verify readiness. |
| `test_read_write_and_retrieval` | Read the old records, write new ones, and retrieve both through public interfaces. |
| `test_authorization` | Verify permitted and denied access and preservation of relevant grants. |
| `test_candidates_and_processing` | Verify retained Candidates and CAS approval; load and acknowledge the old pending intent using the target installed SDK, including idempotency. |
| `test_background_processing` | Run the installed target's actual Memory Worker against a local controlled inference endpoint; capture a new Source, verify the published Memory's exact Source reference and FTS retrieval, and require the child Worker's usage report. |
| `test_preserved_history_and_cursors` | Verify exact history/references and unchanged or declared-transformed cursor and receipt semantics. |
| `test_fresh_upgrade_equivalence` | Initialize a fresh target database; compare schema/backend objects and behavior with the upgraded database. The current suite has no retained/archive object exclusions. |

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
they follow actual installed operations and compare persisted data. The profile exercises explicit writes, FTS and
actual background Memory processing with controlled model responses. Both installed releases declare the Memory
capability and use the same processing ownership; the Worker check changes only the execution interval after migration.
The separate SDK fixture tests the old pending intent's format and acknowledgment. Controlled responses establish
execution and evidence preservation, not real-model quality. Vector storage uses its separately registered profiles;
passing a no-vector case cannot establish vector acceptance.
SQLite inventories include indexes, views and triggers; MySQL-family table DDL includes indexes and constraints,
and views are collected separately. MySQL comparison parses columns, indexes (including parser properties), constraints, and CHECK expressions, ignoring only physical formatting and backend-generated constraint names. Views and triggers are collected independently. SQLite normalizes whitespace and identifier quoting. Structural differences fail rather than being silently excluded.

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


Installed-release execution requires Linux. Each gate command runs under a separate
Linux child subreaper, which owns detached Server and embedded-engine descendants.
On timeout it first interrupts the command so fixtures can close the Server and
seekDB normally, then terminates and reaps any remaining owned descendants before
collecting native logs, deleting the workspace, or starting the next case. Normal
success and failure also clean up leftover descendants. If the gate is terminated,
the owner receives a Linux parent-death signal and completes the same cleanup.
Policy and documentation checks remain available on other platforms.
