"""Cross-tool pipeline recipe runner for locallab."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - tested only when pyyaml is present
    yaml = None


def _now_compact() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z0-9_]+)\}")


def _expand_placeholders(text: str, ctx: dict[str, str]) -> str:
    """Replace {input}, {output}, {timestamp} and any other ctx keys."""
    def repl(match: re.Match) -> str:
        key = match.group(1)
        if key in ctx:
            return ctx[key]
        return match.group(0)
    return _PLACEHOLDER_RE.sub(repl, text)


def _unresolved_placeholders(text: str) -> list[str]:
    """Return placeholder names still present after expansion, e.g. {agent_cmd}."""
    return _PLACEHOLDER_RE.findall(text)


def _load_recipe(path: str) -> dict[str, Any]:
    if yaml is None:
        raise RuntimeError(
            "PyYAML is required to run pipeline recipes. "
            "Install it: pipx inject locallab pyyaml"
        )
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError("recipe must be a YAML mapping")
    return data


def _eval_jq(expr: str, data: Any) -> Any:
    """Evaluate a tiny jq subset used by recipe assertions.

    Supported syntax:
      .key
      .key.nested
      .array[0]
      .array | length
      comparison: ==, !=, <, <=, >, >=
    """
    expr = expr.strip()
    m = re.match(r"^(.+?)\s*(==|!=|<=|>=|<|>)\s*(.+)$", expr)
    if m:
        left_expr, op, right_expr = m.groups()
        left = _eval_jq(left_expr, data)
        right = _eval_jq(right_expr, data)
        if op == "==":
            return left == right
        if op == "!=":
            return left != right
        if op == "<":
            return left < right
        if op == "<=":
            return left <= right
        if op == ">":
            return left > right
        if op == ">=":
            return left >= right
        raise RuntimeError(f"unsupported operator: {op}")

    m = re.match(r"^(.+?)\s*\|\s*length\s*$", expr)
    if m:
        value = _eval_jq(m.group(1), data)
        return len(value) if hasattr(value, "__len__") else 0

    if expr == ".":
        return data

    if expr.startswith("."):
        current = data
        i = 1
        while i < len(expr):
            if expr[i] == ".":
                i += 1
            start = i
            while i < len(expr) and expr[i] not in ".[":
                i += 1
            key = expr[start:i]
            if isinstance(current, dict):
                current = current[key]
            else:
                raise KeyError(f"cannot index {type(current).__name__} with {key!r}")
            if i < len(expr) and expr[i] == "[":
                i += 1
                start = i
                while i < len(expr) and expr[i] != "]":
                    i += 1
                index = int(expr[start:i])
                i += 1
                if isinstance(current, list):
                    current = current[index]
                else:
                    raise IndexError(f"cannot index {type(current).__name__} with [{index}]")
        return current

    # Literal: try JSON parse, then string.
    try:
        return json.loads(expr)
    except (json.JSONDecodeError, ValueError):
        return expr


def _resolve_command(text: str, ctx: dict[str, str], recipe_dir: str) -> list[str]:
    """Expand placeholders and tokenize a run command."""
    expanded = _expand_placeholders(text, ctx)
    tokens = shlex.split(expanded)
    resolved = []
    for tok in tokens:
        resolved_tok = _maybe_resolve_path(tok, recipe_dir)
        resolved.append(resolved_tok)
    return resolved


def _maybe_resolve_path(tok: str, recipe_dir: str) -> str:
    """If a token looks like a relative path, resolve it against recipe_dir."""
    if os.path.isabs(tok):
        return tok
    if tok.startswith("./") or tok.startswith("../"):
        return str((Path(recipe_dir) / tok).resolve())
    # Heuristic: any path separator or a plausible filename with an extension.
    if "/" in tok or "\\" in tok or "." in tok:
        candidate = Path(recipe_dir) / tok
        if candidate.exists():
            return str(candidate.resolve())
    return tok


def _resolve_file(file_path: str, ctx: dict[str, str], recipe_dir: str) -> str:
    """Expand placeholders and resolve a file path against the recipe directory."""
    expanded = _expand_placeholders(file_path, ctx)
    return _maybe_resolve_path(expanded, recipe_dir)


def _build_env(global_env: dict[str, str], step_env: dict[str, str]) -> dict[str, str]:
    env = dict(os.environ)
    env.update(global_env or {})
    env.update(step_env or {})
    return env


def run(recipe_path: str, *, dry_run: bool = False) -> int:
    """Execute a pipeline recipe. Returns the first nonzero exit code, or 0."""
    recipe = _load_recipe(recipe_path)
    recipe_dir = str(Path(recipe_path).resolve().parent)

    timestamp = _now_compact()
    output_dir = Path(recipe_dir) / f"pipeline-output-{timestamp}"
    input_dir = recipe_dir

    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    ctx: dict[str, str] = {
        "input": input_dir,
        "output": str(output_dir),
        "timestamp": timestamp,
    }

    global_env = dict(recipe.get("env") or {})
    global_env = {k: _expand_placeholders(str(v), ctx) for k, v in global_env.items()}

    steps = recipe.get("steps")
    if not isinstance(steps, list):
        print("error: recipe must contain a 'steps' list", file=sys.stderr)
        return 2

    if dry_run:
        print(f"# dry-run: {recipe.get('name', 'unnamed')}")

    for idx, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            print(f"error: step {idx} must be a mapping", file=sys.stderr)
            return 2

        if "run" in step:
            raw_run = str(step["run"])
            command = _resolve_command(raw_run, ctx, recipe_dir)
            env = _build_env(global_env, step.get("env"))
            if dry_run:
                print(f"\n# step {idx} (run)")
                for k, v in (step.get("env") or {}).items():
                    print(f"# env {k}={shlex.quote(str(v))}")
                print(" ".join(shlex.quote(t) for t in command))
                unresolved = _unresolved_placeholders(_expand_placeholders(raw_run, ctx))
                if unresolved:
                    names = ", ".join(f"{{{name}}}" for name in unresolved)
                    print(f"# WARNING: unresolved placeholder(s) {names} — this step would fail on a real run")
                continue
            unresolved = _unresolved_placeholders(_expand_placeholders(raw_run, ctx))
            if unresolved:
                names = ", ".join(f"{{{name}}}" for name in unresolved)
                print(
                    f"error: step {idx} has unresolved placeholder(s) {names} — "
                    "define them under the recipe's 'env' or pass them another way; "
                    "the raw placeholder text would otherwise be run as a literal argument",
                    file=sys.stderr,
                )
                return 2
            print(f"\n[step {idx}] {' '.join(shlex.quote(t) for t in command)}", file=sys.stderr)
            result = subprocess.run(command, env=env)
            if result.returncode != 0:
                print(f"error: step {idx} failed with exit code {result.returncode}", file=sys.stderr)
                return result.returncode

        elif "assert" in step:
            assertion = step["assert"]
            if not isinstance(assertion, dict):
                print(f"error: step {idx} assert must be a mapping", file=sys.stderr)
                return 2
            file_path = assertion.get("file")
            jq_expr = assertion.get("jq")
            exists_only = assertion.get("exists")
            if not file_path or (not jq_expr and not exists_only):
                print(f"error: step {idx} assert needs 'file' plus either 'jq' or 'exists'", file=sys.stderr)
                return 2
            resolved_file = _resolve_file(file_path, ctx, recipe_dir)
            if dry_run:
                print(f"\n# step {idx} (assert)")
                print(f"# file {resolved_file}")
                print(f"# exists {exists_only}" if exists_only else f"# jq {jq_expr}")
                continue
            if exists_only:
                if not os.path.exists(resolved_file):
                    print(f"ASSERTION FAILED: step {idx} ({resolved_file} does not exist)", file=sys.stderr)
                    return 1
                print(f"[step {idx}] assertion passed: {resolved_file} exists", file=sys.stderr)
                continue
            try:
                with open(resolved_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError) as exc:
                print(f"error: step {idx} cannot read {resolved_file} as JSON: {exc}", file=sys.stderr)
                return 1
            value = _eval_jq(jq_expr, data)
            if value is not True:
                print(f"ASSERTION FAILED: step {idx} ({jq_expr}) -> {value}", file=sys.stderr)
                return 1
            print(f"[step {idx}] assertion passed: {jq_expr}", file=sys.stderr)

        else:
            print(f"error: step {idx} must have 'run' or 'assert'", file=sys.stderr)
            return 2

    if dry_run:
        print("\n# dry-run complete")
    else:
        print("\npipeline complete")
    return 0
