---
id: "5fca53d2"
type: decision
tags: ["locallab", "pipeline", "recipes", "jq", "cli"]
created: "2026-10-12T17:59:33.638Z"
source: manual
---
LocalLab umbrella: 2026-10 audit fixes (branch fix/umbrella-audit)
- Recipe steps run WITHOUT a shell, using shlex POSIX split. `{input}`, `{output}` and `{recipe_dir}` expand with forward slashes so Windows paths survive the split. Any placeholder left unresolved is a hard error (exit 2) before any step runs. Values come from `vars:` and `--var`.
- Only `./` and `../` run tokens resolve against the recipe dir. The old "file exists next to the recipe" heuristic was removed because it mangled values like `fs.v2`.
- The jq subset is a home-grown recursive-descent parser. It deliberately does NOT shell out to real `jq`, so results are deterministic. `==` is type-aware (bool never equals a number). Mixed-type ordering raises an error instead of using jq's total type order.
- `install-all` installs from PyPI by default. Tools already installed are upgraded. `--git` reinstalls with `pipx install --force git+...`.
- `doctor`: missing tools are SKIP (exit 0) unless `--strict`; only broken tools fail.
- `init` scaffolds `.locallab/tools.json` as `[]`, which is the toolcall-linter schema file, not the locallab tool registry.
