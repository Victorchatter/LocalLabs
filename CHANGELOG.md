# LocalLab umbrella changelog

## Unreleased

### Fixed
- Both packaged recipes failed to parse: unquoted `file: {output}/...` is a
  YAML flow mapping. Placeholder values are now quoted.
- A recipe YAML error or missing recipe file prints
  `error: cannot parse recipe <path>: ...` and exits 2 instead of a traceback.
- Unresolved placeholders such as `{agent_cmd}` were passed through literally;
  every step is now resolved before anything runs and a leftover placeholder
  exits 2 with the `--var` to pass (dry-run and real runs).
- `{input}`/`{output}` defaulted to the recipe's directory, so running a
  packaged recipe pointed them into site-packages. They now default to the current
  directory and `<cwd>/pipeline-output-<timestamp>`.
- The output directory was never created; it is now created before the first
  real step (never on `--dry-run`).
- Run tokens such as `fs.v2` were rewritten to a path whenever a same-named file
  sat next to the recipe. Only `./` and `../` tokens resolve against the recipe
  directory now.
- Built-in paths use forward slashes so POSIX command splitting no longer eats
  Windows backslashes in `{input}`/`{output}`/`{recipe_dir}`.
- The regex jq evaluator split on operators inside string literals
  (`.s == "a==b"`), raised on missing keys, and leaked tracebacks. It is now a
  tokenizer plus recursive-descent parser with jq null semantics; evaluation
  errors are reported as `ASSERTION FAILED ... -> error: ...` (exit 1).
- `hydrate-replay` ran a jq assertion against a SQLite file; it now asserts the
  cache database exists.
- A missing run executable is reported as a step error instead of a traceback.
- `__version__` was hard-coded to 0.1.0; it now comes from package metadata.
- `agent-checkpoint` installs as `localab-checkpoint` (the PyPI name).
- `install-all` installed from GitHub and failed on tools pipx already had; it
  now installs from PyPI and upgrades already-installed tools.
- A missing `pipx` printed a traceback in `install-all`/`update`; it now prints
  `error: pipx not found on PATH ...` and exits 1.
- `doctor` failed when a tool was simply not installed; missing tools are now
  `SKIP (missing)` and only fail with `--strict`.
- `versions` reported both missing and broken tools as "not installed", and
  missed `v`-prefixed and two-part versions.
- selfcheck fake tools now work on Linux without `python3` on PATH and on
  Windows without `.PY` in `PATHEXT`; the real-run pipeline check no longer
  leaves an output directory in the repo.

### Added
- `locallab pipeline --list`, packaged recipes runnable by name
  (`locallab pipeline record-audit-lint ...`), and repeatable `--var KEY=VAL`.
- Recipe `vars:` defaults and the `{recipe_dir}` placeholder.
- `assert: {exists: path}`.
- Step options `if:` and `continue-on-error:`.
- jq subset: `|`, `//`, `and`, `or`, `not`, `length`, parentheses, negative
  indexes, string/number/boolean/null literals.
- `locallab install-all --git` to install from the GitHub repos (already-installed
  tools are reinstalled from GitHub with `pipx install --force`).
- `locallab doctor --strict` and `--json`.
- `locallab init [--force]`: scaffolds `.locallab/tools.json`,
  `.locallab/recipe.yaml` and `.github/workflows/locallab.yml`.
- pytest suite under `tests/` and a CI workflow (Ubuntu + Windows,
  Python 3.10 and 3.13) running `selfcheck.py` and `pytest`.
- Python 3.13 classifier.

## 0.2.0 — 2026-07-28

### Added
- New `examples/` cookbook directory with five runnable recipes:
  - cap Claude Code spend through `agent-circuit-breaker`
  - cache an MCP filesystem server with `toolcall-cache`
  - turn an `agent-vcr` tape into a regression test with `transcript-to-test`
  - lint prompts with `prompt-portability-linter` before provider migration
  - save and resume a long run with `agent-checkpoint`
- README sections linking the cookbook and published benchmark results.
- `selfcheck.py` validates that every cookbook recipe has the required
  structure and code blocks.

## 0.1.0 — 2026-07-28

### Added
- New `locallab` umbrella CLI package (`pipx install git+https://github.com/Victorchatter/LocalLab.git`).
- `locallab install-all`: install all 10 LocalLab tools via `pipx`.
- `locallab update`: run `pipx upgrade` for every LocalLab tool.
- `locallab versions`: print installed versions (offline).
- `locallab doctor`: deterministic smoke-test for every installed tool.
- `locallab pipeline <recipe.yaml>`: cross-tool recipe runner with placeholders, per-step and global `env`, `--dry-run`, and `jq`-like assertions.
- `locallab/data/tools.json`: mapping of tool names to repos, packages, and smoke commands.
- Example recipes in `locallab/recipes/`.
- `selfcheck.py` covering `versions`, `doctor`, and `pipeline` (fully offline).
