# LocalLab umbrella changelog

## Unreleased

### Fixed
- Both shipped `locallab/recipes/*.yaml` files were invalid YAML: an
  unquoted `{output}`/`{input}` placeholder as the first character of a
  scalar (e.g. `file: {output}/audit.json`) parses as a YAML flow mapping,
  so `locallab pipeline` failed immediately with a `ParserError` before
  running a single step. Placeholders are now quoted.
- `hydrate-replay.yaml`'s final assertion ran `jq: '.'` against
  `toolcall-cache`'s SQLite `cache.db`, which crashed with an uncaught
  `UnicodeDecodeError` (not a subclass of the caught `OSError`/
  `json.JSONDecodeError`) since the file isn't JSON. `pipeline.py` assert
  steps now support `exists: true` for a plain file-existence check, and
  the recipe uses it instead of parsing a binary file as JSON. The
  remaining JSON-parse path now also catches `ValueError` generally (which
  covers `UnicodeDecodeError` too), so a non-UTF8/non-JSON file under a
  `jq` assertion fails cleanly instead of crashing the whole run.
- `locallab pipeline` silently left unresolved `{placeholder}` text (e.g.
  `{agent_cmd}`, which recipes reference but nothing defines) in commands
  passed straight to the subprocess. A real run now fails fast with a
  clear error naming the unresolved placeholder instead of executing a
  broken command; `--dry-run` prints a warning instead of failing, so the
  preview still shows the whole recipe.
- README's "Install the whole lab" `pipx install` list and the tools table
  both named the `agent-checkpoint` package `localab-checkpoint`, which
  doesn't exist — only `agent-vcr` and `agent-circuit-breaker` were
  actually renamed on PyPI (`localab-vcr`, `localab-circuit-breaker`; see
  `locallab/data/tools.json`, which `locallab update`/`install-all` already
  used correctly). Fixed both README references to `agent-checkpoint`.
- `selfcheck.py`'s fake-tool helper wrote `<name>.py` on every platform and
  relied on `shutil.which(name)` (no extension) to find it. That only
  works on Windows, where PATHEXT-based lookup is used; on POSIX,
  `shutil.which` requires an exact filename match, so every fake tool
  silently resolved to "not installed" and `check_versions`/`check_doctor`
  failed on Linux/macOS. There is no CI workflow in this repo, so nothing
  caught it. Fake tools are now written without a `.py` suffix on POSIX.
- Added `check_shipped_recipes()` to `selfcheck.py`, which dry-runs every
  file under `locallab/recipes/` so a YAML/placeholder regression in a
  shipped recipe (as above) fails the selfcheck instead of only surfacing
  when a user runs the recipe for real.

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
