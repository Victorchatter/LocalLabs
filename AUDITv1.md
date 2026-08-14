# LocalLab — Umbrella Audit v1 (2026-08-14)

Scope note up front: **LocalLab is the umbrella/index repo, not a monorepo.**
The ten tools it lists (`agent-vcr`, `tokenauditor`, `toolcall-linter`,
`transcript-to-test`, `agent-circuit-breaker`, `toolcall-cache`,
`agent-checkpoint`, `transcript-bridge`, `mcp-openai-bridge`,
`prompt-portability-linter`) each live in their own separate repo. This audit
only inspected code that actually lives in `Victorchatter/LocalLab`
(`locallab/cli.py`, `locallab/pipeline.py`, `locallab/__init__.py`,
`locallab/data/tools.json`, `locallab/recipes/*.yaml`, `selfcheck.py`,
`pyproject.toml`, and the docs). It does not — and could not — inspect the
nine tool repos' own source. Findings below are either bugs in the umbrella
CLI itself, or cross-repo *integration* problems visible from this repo's
docs and data files (name mismatches, broken examples, doc drift).

All findings were verified by reading the actual code paths, not inferred.

---

## Bugs

### 1. `locallab --version` reports the wrong version
`pyproject.toml` declares `version = "0.2.0"`, but `locallab/__init__.py`
hardcodes `__version__ = "0.1.0"` and `cli.py`'s `--version` flag prints that
constant. The two have drifted since the 0.2.0 release (CHANGELOG.md
confirms 0.2.0 shipped, `__init__.py` was never bumped).
- **Fix:** derive `__version__` from installed package metadata
  (`importlib.metadata.version("locallab")`) instead of a hand-maintained
  constant, so it can't drift from `pyproject.toml` again. Bump the constant
  now regardless.

### 2. `{agent_cmd}` is never defined — recipes silently ship a literal `{agent_cmd}` token
Both shipped recipes (`locallab/recipes/record-audit-lint.yaml`, the one
reproduced verbatim in the README's "🌂 umbrella CLI" section, and
`locallab/recipes/hydrate-replay.yaml`) reference `{agent_cmd}`. But
`pipeline.py`'s `ctx` only ever contains `input`, `output`, `timestamp`, and
whatever's under a recipe's `env:` block — there is no CLI flag or recipe
section that supplies `agent_cmd`. `_expand_placeholders()` leaves *any*
unrecognized `{...}` token untouched (`return match.group(0)`), so a real
(non `--dry-run`) run of the README's own flagship pipeline example passes
the literal 12-character string `{agent_cmd}` as an argv token to
`agent-vcr record -- {agent_cmd}`. The documented example is broken as
shipped.
- **Fix:** add a repeatable `--set key=value` flag to `locallab pipeline`
  (and/or a `vars:` block in the recipe schema) to supply run-specific
  values. Separately — and regardless of that feature — make
  `_resolve_command`/`run()` **fail fast** with a clear
  `error: unresolved placeholder {agent_cmd} in step N` if any `{...}`
  survives expansion, instead of forwarding it to the subprocess.

### 3. `{output}` directory is referenced everywhere but never created
`pipeline.py::run()` computes
`output_dir = Path(recipe_dir) / f"pipeline-output-{timestamp}"` and exposes
it as `{output}`, but nothing ever calls `output_dir.mkdir()`. Every `run:`
step in both shipped recipes writes into `{output}/...`
(`{output}/tape.jsonl`, `{output}/audit.json`, `{output}/lint.sarif`,
`{output}/.locallab/toolcall-cache/cache.db`). Unless the first tool in the
chain happens to `mkdir -p` its own output path (not something the umbrella
controls or can assume), the very first step of every recipe fails with a
"directory does not exist" error from the underlying tool.
- **Fix:** `output_dir.mkdir(parents=True, exist_ok=True)` in `run()` before
  the step loop (skip when `dry_run=True`).

### 4. Shipped `record-audit-lint.yaml` points at a `tools.json` that doesn't exist
Step 3 of `locallab/recipes/record-audit-lint.yaml` runs
`toolcall-linter {output}/tape.jsonl --tools {input}/tools.json ...`.
`{input}` resolves to the recipe's own directory (`locallab/recipes/`), but
the real `tools.json` lives at `locallab/data/tools.json` — there is no
`tools.json` under `locallab/recipes/`. Running the recipe exactly as
documented fails at this step.
- **Fix:** either ship a `locallab/recipes/tools.json` fixture next to the
  recipe, or resolve a dedicated ctx var (e.g. `{repo_root}` /
  `{tools_json}`) that always points at `locallab/data/tools.json`. Then add
  a selfcheck that actually *executes* the shipped recipes end-to-end against
  fake tools (see Bug 8) instead of only exercising a synthetic
  `demo.yaml`.

### 5. `mcp-openai-bridge` may be keyed under the wrong binary name
`tools.json`, the README's tool table, and `cli.py` all address this tool as
`mcp-openai-bridge`, and `doctor`/`versions`/`install-all` all call
`shutil.which("mcp-openai-bridge")`. But `PROJECTS.md`'s own documented CLI
example for the same project is:
```
CLI: mcp2openai --config mcp-servers.json
```
a *different* binary name. If `mcp2openai` is what actually gets installed
on `PATH`, then `locallab doctor`/`locallab versions` will report this tool
as failing/not-installed forever, even on a correct install — and any
pipeline recipe that shells out to `mcp-openai-bridge` will hit the
`command not found` fallback path in `_tool_command`.
- **Fix:** add an optional `"bin"` field to `tools.json` (defaulting to the
  tool's key when absent) so the umbrella can resolve the real installed
  executable name independently of the display name, and reconcile
  `PROJECTS.md` vs `README.md` vs `tools.json` on which name is canonical.

### 6. A malformed `assert.jq` expression crashes the whole pipeline with a raw traceback
In `pipeline.py::run()`, the `assert` branch only catches
`(OSError, json.JSONDecodeError)` around the file read. `_eval_jq()` itself
can raise a bare `KeyError` (missing key), `IndexError` (out-of-range array
index), or `TypeError` (indexing a non-dict/non-list) — none of which are
caught. That exception propagates out of `run()` uncaught, so a typo in a
recipe's `jq:` expression (e.g. referencing `.total_cost` on a JSON file
that doesn't have that key) kills the run with a Python traceback instead of
the clean `error: ...` / nonzero-exit style used everywhere else in this
file.
- **Fix:** wrap the `_eval_jq(...)` call in `try/except (KeyError, IndexError,
  TypeError) as exc` and print
  `error: step N assertion could not be evaluated ({jq_expr}): {exc}`,
  return 1.

### 7. `install-all` / `update` have no subprocess timeout
`_run_tool()` (used by `doctor`/`versions`) passes `timeout=30`. `_pipx_run()`
(used by `install-all`/`update`) does not pass any `timeout` to
`subprocess.run`. A stuck network resolution or a `pipx install` waiting on
an interactive prompt will hang `locallab install-all`/`locallab update`
indefinitely with no way to recover short of killing the process.
- **Fix:** give `_pipx_run` a sane default timeout (e.g. a few minutes,
  configurable) and surface a clear "timed out installing/upgrading X"
  message rather than hanging.

### 8. `install-all` and `update` don't target the same install, and neither matches the README
`cmd_install_all` always installs from source —
`git+https://github.com/Victorchatter/{repo}.git` — ignoring `tools.json`'s
`package` field entirely except for display. `cmd_update` upgrades by PyPI
package name — `pipx upgrade {package}`. These are two different install
sources for what's supposed to be the same tool, and neither is
consistent with README.md's own "📥 Install the whole lab" section, which
tells users to `pipx install <pypi-name>` directly (no `git+https`). If a
user's first install came from `locallab install-all` (git source) and pipx
happens to name the resulting app differently than the PyPI package name in
`tools.json`, `locallab update`/`versions` will silently treat it as "not
installed."
- **Fix:** make `install-all` install from PyPI by default (matching
  `update` and the README), with an explicit opt-in flag (`--from-source` /
  `--git`) for the `git+https` path when someone wants bleeding-edge. Add a
  selfcheck assertion that install-all's target and update's target resolve
  to the same pipx app name for every entry in `tools.json`.

### 9. (minor) `pyyaml` is both a hard dependency and defensively optional
`pyproject.toml` lists `pyyaml>=6.0` as a hard runtime dependency, so it is
always present after `pipx install locallab`. But `pipeline.py` still guards
`import yaml` with `try/except ImportError` and prints an "install pyyaml"
hint — a code path that can't be reached through the packaged distribution.
Harmless today, but it's confusing dead code that suggests pyyaml is
optional when it isn't.
- **Fix:** either drop the guard (simpler, matches the declared dependency),
  or if the intent really is "pipeline works without pyyaml for non-YAML
  recipes," make that intent explicit and covered by a selfcheck case.

---

## Feature proposals

Ranked roughly by leverage-for-effort.

1. **`locallab pipeline --check <recipe.yaml>`** — a static-lint / dry-run-plus
   mode that (a) confirms every tool referenced in `run:` steps is on PATH,
   and (b) confirms every `{placeholder}` in the recipe resolves against
   `ctx` + `env:` + any `--set` vars, *without* executing anything. This
   would have caught Bugs 2–4 automatically before either recipe shipped,
   and gives contributors a pre-flight check for new recipes.

2. **`--set key=value` (repeatable) and/or a `vars:` recipe block** for
   `locallab pipeline` — the concrete fix for Bug 2, and generally useful:
   every interesting recipe needs at least one caller-supplied value
   (an agent command, a budget, a target directory).

3. **`locallab list`** — print the ten tools with their category,
   one-line description, and install status, sourced from `tools.json` (plus
   a short description field that `tools.json` doesn't currently carry).
   Right now the only way to see the roster + status is to cross-reference
   `README.md`/`PROJECTS.md` by hand; `versions`/`doctor` show install
   health but not what each tool *does*. Add `--json` for scripting.

4. **Parallelize `doctor`/`versions` tool checks.** Both commands currently
   shell out to each of the 10 tools sequentially, each with up to a 30s
   timeout. A single hung or slow tool serializes the whole health check to
   minutes. A small thread pool (bounded, e.g. `ThreadPoolExecutor(max_workers=5)`)
   would cap total wall time to roughly the slowest single tool instead of
   the sum of all ten.

5. **Retry / continue-on-error at the recipe-step level.** Real agent runs
   (`agent-vcr record`) can be flaky for reasons unrelated to the pipeline
   itself (model API hiccup, MCP server cold start). A per-step
   `retry: N` and/or `continue-on-error: true` in the recipe schema would
   make CI usage of `locallab pipeline` far less brittle without requiring
   every downstream tool to implement its own retry logic.

6. **Execute (not just structurally validate) the shipped recipes in CI.**
   `selfcheck.py` today only dry-runs a synthetic `demo.yaml` and checks that
   the cookbook markdown files contain the right section headers — it never
   actually runs `locallab/recipes/record-audit-lint.yaml` or
   `hydrate-replay.yaml` against fake tool stubs the way `check_versions()`/
   `check_doctor()` already do for the umbrella commands. Extending the
   existing fake-tool-on-PATH pattern to also execute the two shipped
   recipes end-to-end (with fake `agent-vcr`/`tokenauditor`/etc. writing
   fixture JSON) would be the single highest-value addition to
   `selfcheck.py` here — it's exactly what would have caught Bugs 2–4 before
   release.

7. **`--vars-file`** for `locallab pipeline`, loading a small YAML/`.env` of
   key/value pairs — pairs naturally with proposal 2 for CI use where
   `--set` chains get unwieldy (e.g. wiring `agent_cmd` in from a GitHub
   Actions matrix).

8. **Reconcile install-source docs across README.md / PROJECTS.md /
   `tools.json`.** README.md's per-tool table uses PyPI names
   (`pipx install tokenauditor`); PROJECTS.md's per-tool entries all use
   `git+https://github.com/...` source installs for the same ten tools.
   Pick one as canonical (PyPI, since that's what `pipx install locallab &&
   locallab install-all` should arguably also do — see Bug 8) and make all
   three sources of truth agree.

---

## Out of scope for this pass

The nine tool repos themselves (`AgentVCR`, `Tokenauditor`, `toolcall-linter`,
`transcript-to-test`, `agent-circuit-breaker`, `toolcall-cache`,
`agent-checkpoint`, `transcript-bridge`, `mcp-openai-bridge`,
`prompt-portability-linter`) were not inspected — this session's repository
access is scoped to `Victorchatter/LocalLab` only. A follow-up audit against
each tool repo individually would be needed to find bugs/features inside
those CLIs themselves (this repo's docs claim each already has its own
`selfcheck.py`/CI, benchmarks, etc., which is a reasonable starting point for
that follow-up).
