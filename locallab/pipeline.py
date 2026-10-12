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
from typing import Any, Callable

try:
    import yaml
except ImportError:  # pragma: no cover - tested only when pyyaml is present
    yaml = None


RECIPES_DIR = Path(__file__).resolve().parent / "recipes"
_PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_]+)\}")
_FALSY = {"", "0", "false", "no", "off"}


class JqError(ValueError):
    """A jq expression could not be parsed or evaluated."""


def packaged_recipes() -> dict[str, Path]:
    """Return recipe name (file stem) -> path for recipes shipped with locallab."""
    return {p.stem: p for p in sorted(RECIPES_DIR.glob("*.yaml"))}


def _now_compact() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _expand_placeholders(text: str, ctx: dict[str, str]) -> str:
    """Replace {input}, {output}, {timestamp} and any other ctx keys."""
    def repl(match: re.Match) -> str:
        return ctx.get(match.group(1), match.group(0))
    return _PLACEHOLDER.sub(repl, text)


def _load_recipe(path: str) -> dict[str, Any]:
    if yaml is None:
        raise RuntimeError(
            "PyYAML is required to run pipeline recipes. "
            "Install it: pipx inject locallab pyyaml"
        )
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(str(exc)) from exc
    if not isinstance(data, dict):
        raise ValueError("recipe must be a YAML mapping")
    return data


# --- jq subset -------------------------------------------------------------

_JQ_TOKEN = re.compile(
    r"""
    (?P<str>"(?:[^"\\]|\\.)*")
    |(?P<op>//|==|!=|<=|>=|[<>|()\[\]])
    |(?P<field>\.[A-Za-z_][A-Za-z0-9_]*)
    |(?P<dot>\.)
    |(?P<num>-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
    |(?P<ident>[A-Za-z_][A-Za-z0-9_]*)
    """,
    re.VERBOSE,
)
_CMP_OPS = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
}


def _jq_tokenize(expr: str) -> list[tuple[str, Any]]:
    tokens: list[tuple[str, Any]] = []
    pos = 0
    while True:
        while pos < len(expr) and expr[pos].isspace():
            pos += 1
        if pos >= len(expr):
            return tokens
        m = _JQ_TOKEN.match(expr, pos)
        if not m:
            raise JqError(f"unexpected character {expr[pos]!r} at {pos}")
        kind, text = m.lastgroup, m.group()
        pos = m.end()
        if kind in ("str", "num"):
            try:
                tokens.append(("lit", json.loads(text)))
            except ValueError as exc:
                raise JqError(f"bad literal {text}: {exc}") from exc
        elif kind == "field":
            tokens.append(("field", text[1:]))
        elif kind == "dot":
            tokens.append(("dot", text))
        elif kind == "op":
            tokens.append((text, text))
        else:
            tokens.append(("ident", text))


def _jq_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return "array" if isinstance(value, list) else "object"


def _jq_truthy(value: Any) -> bool:
    return value is not None and value is not False


def _jq_index(value: Any, key: str | int) -> Any:
    if value is None:
        return None
    if isinstance(key, str):
        if not isinstance(value, dict):
            raise JqError(f"cannot index {_jq_type(value)} with {key!r}")
        return value.get(key)
    if not isinstance(value, list):
        raise JqError(f"cannot index {_jq_type(value)} with [{key}]")
    try:
        return value[key]
    except IndexError:
        return None


def _jq_length(value: Any) -> Any:
    if value is None:
        return 0
    if isinstance(value, bool):
        raise JqError("boolean has no length")
    if isinstance(value, (int, float)):
        return abs(value)
    if isinstance(value, (str, list, dict)):
        return len(value)
    raise JqError(f"{_jq_type(value)} has no length")


def _jq_compare(op: str, left: Callable, right: Callable) -> Callable:
    # ponytail: Python equality, so `true == 1` is true (jq says false) and
    # null/mixed-type ordering raises instead of using jq's total order;
    # upgrade path: compare (type rank, value) tuples when a recipe needs it.
    def fn(data: Any) -> Any:
        try:
            return _CMP_OPS[op](left(data), right(data))
        except TypeError as exc:
            raise JqError(str(exc)) from exc
    return fn


def _jq_alternative(left: Callable, right: Callable) -> Callable:
    def fn(data: Any) -> Any:
        value = left(data)
        return value if _jq_truthy(value) else right(data)
    return fn


class _JqParser:
    """Recursive descent over: pipe > // > or > and > comparison > term."""

    def __init__(self, tokens: list[tuple[str, Any]]):
        self.tokens = tokens
        self.i = 0

    def peek(self) -> tuple[str, Any] | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def take(self, kind: str | None = None) -> tuple[str, Any]:
        tok = self.peek()
        if tok is None or (kind is not None and tok[0] != kind):
            want = kind or "a term"
            got = "end of expression" if tok is None else repr(tok[1])
            raise JqError(f"expected {want}, got {got}")
        self.i += 1
        return tok

    def at(self, kind: str, value: Any = None) -> bool:
        tok = self.peek()
        return tok is not None and tok[0] == kind and (value is None or tok[1] == value)

    def parse(self) -> Callable:
        fn = self.pipe()
        if self.peek() is not None:
            raise JqError(f"unexpected token {self.peek()[1]!r}")
        return fn

    def pipe(self) -> Callable:
        left = self.alternative()
        if self.at("|"):
            self.take()
            right = self.pipe()
            return lambda d, left=left: right(left(d))
        return left

    def alternative(self) -> Callable:
        left = self.or_()
        while self.at("//"):
            self.take()
            left = _jq_alternative(left, self.or_())
        return left

    def or_(self) -> Callable:
        left = self.and_()
        while self.at("ident", "or"):
            self.take()
            right = self.and_()
            left = lambda d, l=left, r=right: _jq_truthy(l(d)) or _jq_truthy(r(d))
        return left

    def and_(self) -> Callable:
        left = self.comparison()
        while self.at("ident", "and"):
            self.take()
            right = self.comparison()
            left = lambda d, l=left, r=right: _jq_truthy(l(d)) and _jq_truthy(r(d))
        return left

    def comparison(self) -> Callable:
        left = self.term()
        tok = self.peek()
        if tok is not None and tok[0] in _CMP_OPS:
            self.take()
            left = _jq_compare(tok[0], left, self.term())
            nxt = self.peek()
            if nxt is not None and nxt[0] in _CMP_OPS:
                raise JqError(f"comparison operators are non-associative: {nxt[0]!r}")
        return left

    def term(self) -> Callable:
        kind, value = self.take()
        if kind in ("dot", "field"):
            keys: list[str | int] = [] if kind == "dot" else [value]
            while True:
                if self.at("field"):
                    keys.append(self.take()[1])
                elif self.at("["):
                    self.take()
                    _, index = self.take("lit")
                    if not isinstance(index, int) or isinstance(index, bool):
                        raise JqError(f"array index must be an integer, got {index!r}")
                    self.take("]")
                    keys.append(index)
                else:
                    break

            def path(d: Any, keys: list = keys) -> Any:
                for key in keys:
                    d = _jq_index(d, key)
                return d
            return path
        if kind == "lit":
            return lambda d: value
        if kind == "(":
            inner = self.pipe()
            self.take(")")
            return inner
        if kind == "ident":
            if value in ("true", "false", "null"):
                literal = {"true": True, "false": False, "null": None}[value]
                return lambda d: literal
            if value == "length":
                return _jq_length
            if value == "not":
                return lambda d: not _jq_truthy(d)
        raise JqError(f"unexpected {value!r}")


def _eval_jq(expr: str, data: Any) -> Any:
    """Evaluate the supported jq subset (see README) against parsed JSON."""
    return _JqParser(_jq_tokenize(str(expr))).parse()(data)


# --- recipe resolution -----------------------------------------------------

def _maybe_resolve_path(tok: str, recipe_dir: str) -> str:
    """Resolve ./ and ../ run tokens against recipe_dir; pass everything else through."""
    if tok.startswith("./") or tok.startswith("../"):
        return str((Path(recipe_dir) / tok).resolve())
    return tok


def _resolve_file(file_path: str, recipe_dir: str) -> str:
    """Resolve an (already expanded) assert path; relative paths use recipe_dir."""
    return str(Path(recipe_dir) / file_path)


def _build_env(global_env: dict[str, str], step_env: dict[str, str]) -> dict[str, str]:
    env = dict(os.environ)
    env.update(global_env or {})
    env.update(step_env or {})
    return env


def _build_ctx(recipe: dict[str, Any], recipe_dir: str, variables: dict[str, str] | None) -> dict[str, str]:
    timestamp = _now_compact()
    # Forward slashes: run lines go through POSIX shlex.split, which would eat
    # Windows backslashes. Windows APIs accept either separator.
    cwd = Path.cwd()
    base = {
        "input": cwd.as_posix(),
        "output": (cwd / f"pipeline-output-{timestamp}").as_posix(),
        "timestamp": timestamp,
        "recipe_dir": Path(recipe_dir).as_posix(),
    }
    ctx = dict(base)
    for key, value in (recipe.get("vars") or {}).items():
        ctx[str(key)] = _expand_placeholders(str(value), base)
    ctx.update(variables or {})
    return ctx


def _expand_env(env: Any, ctx: dict[str, str]) -> dict[str, str]:
    return {str(k): _expand_placeholders(str(v), ctx) for k, v in (env or {}).items()}


def _prepare_step(idx: int, step: Any, ctx: dict[str, str], global_env: dict[str, str]) -> tuple[dict, str]:
    """Validate and expand one step. Returns (plan, error message or "")."""
    if not isinstance(step, dict):
        return {}, f"error: step {idx} must be a mapping"
    plan: dict[str, Any] = {
        "env": _expand_env(step.get("env"), ctx),
        "if": _expand_placeholders(str(step["if"]), ctx) if "if" in step else None,
        "continue": step.get("continue-on-error") is True,
    }
    texts = [*global_env.values(), *plan["env"].values()]
    if plan["if"] is not None:
        texts.append(plan["if"])

    if "run" in step:
        plan["kind"] = "run"
        plan["text"] = _expand_placeholders(str(step["run"]), ctx)
        texts.append(plan["text"])
    elif "assert" in step:
        assertion = step["assert"]
        keys = set(assertion) if isinstance(assertion, dict) else set()
        if keys == {"file", "jq"} and assertion["file"] and assertion["jq"]:
            plan.update(kind="jq", path=str(assertion["file"]), jq=str(assertion["jq"]))
        elif keys == {"exists"} and assertion["exists"]:
            plan.update(kind="exists", path=str(assertion["exists"]))
        else:
            return {}, f"error: step {idx} assert needs 'file'+'jq' or 'exists'"
        plan["path"] = _expand_placeholders(plan["path"], ctx)
        texts.append(plan["path"])
    else:
        return {}, f"error: step {idx} must have 'run' or 'assert'"

    missing = list(dict.fromkeys(m for t in texts for m in _PLACEHOLDER.findall(t)))
    if missing:
        names = ", ".join(f"{{{m}}}" for m in missing)
        hint = " ".join(f"--var {m}=..." for m in missing)
        return {}, (
            f"error: step {idx}: unresolved placeholder(s): {names} — "
            f"pass {hint} or add it to the recipe's vars:"
        )

    if plan["kind"] == "run":
        try:
            plan["command"] = [_maybe_resolve_path(t, ctx["recipe_dir"]) for t in shlex.split(plan["text"])]
        except ValueError as exc:
            return {}, f"error: step {idx}: cannot parse run command: {exc}"
        if not plan["command"]:
            return {}, f"error: step {idx}: empty run command"
    else:
        plan["path"] = _resolve_file(plan["path"], ctx["recipe_dir"])
    return plan, ""


def _check_assertion(idx: int, plan: dict) -> bool:
    """Run one assertion, printing the outcome. Returns True when it passes."""
    if plan["kind"] == "exists":
        if os.path.exists(plan["path"]):
            print(f"[step {idx}] assertion passed: exists {plan['path']}", file=sys.stderr)
            return True
        print(f"ASSERTION FAILED: step {idx} (exists {plan['path']}) -> false", file=sys.stderr)
        return False
    expr = plan["jq"]
    try:
        with open(plan["path"], "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        print(f"error: step {idx} cannot read {plan['path']}: {exc}", file=sys.stderr)
        return False
    try:
        value = _eval_jq(expr, data)
    except (JqError, KeyError, TypeError, ValueError, IndexError) as exc:
        print(f"ASSERTION FAILED: step {idx} ({expr}) -> error: {exc}", file=sys.stderr)
        return False
    if value is not True:
        print(f"ASSERTION FAILED: step {idx} ({expr}) -> {value}", file=sys.stderr)
        return False
    print(f"[step {idx}] assertion passed: {expr}", file=sys.stderr)
    return True


def _print_dry_run(idx: int, plan: dict) -> None:
    if plan["kind"] == "run":
        print(f"\n# step {idx} (run)")
        for k, v in plan["env"].items():
            print(f"# env {k}={shlex.quote(v)}")
        print(" ".join(shlex.quote(t) for t in plan["command"]))
        return
    print(f"\n# step {idx} (assert)")
    if plan["kind"] == "exists":
        print(f"# exists {plan['path']}")
    else:
        print(f"# file {plan['path']}")
        print(f"# jq {plan['jq']}")


def run(recipe_path: str, *, dry_run: bool = False, variables: dict[str, str] | None = None) -> int:
    """Execute a pipeline recipe. Returns the first nonzero exit code, or 0."""
    try:
        recipe = _load_recipe(recipe_path)
    except ValueError as exc:
        print(f"error: cannot parse recipe {recipe_path}: {exc}", file=sys.stderr)
        return 2
    recipe_dir = str(Path(recipe_path).resolve().parent)

    if not isinstance(recipe.get("vars") or {}, dict):
        print("error: recipe 'vars' must be a mapping", file=sys.stderr)
        return 2
    ctx = _build_ctx(recipe, recipe_dir, variables)
    global_env = _expand_env(recipe.get("env"), ctx)

    steps = recipe.get("steps")
    if not isinstance(steps, list):
        print("error: recipe must contain a 'steps' list", file=sys.stderr)
        return 2

    plans = []
    for idx, step in enumerate(steps, start=1):
        plan, err = _prepare_step(idx, step, ctx, global_env)
        if err:
            print(err, file=sys.stderr)
            return 2
        plans.append(plan)

    if dry_run:
        print(f"# dry-run: {recipe.get('name', 'unnamed')}")

    for idx, plan in enumerate(plans, start=1):
        cond = plan["if"]
        if cond is not None and cond.strip().lower() in _FALSY:
            if dry_run:
                print(f"\n# step {idx} skipped (if: {cond})")
            else:
                print(f"\n[step {idx}] skipped (if: {cond})", file=sys.stderr)
            continue
        if dry_run:
            _print_dry_run(idx, plan)
            continue

        Path(ctx["output"]).mkdir(parents=True, exist_ok=True)
        if plan["kind"] == "run":
            command = plan["command"]
            print(f"\n[step {idx}] {' '.join(shlex.quote(t) for t in command)}", file=sys.stderr)
            try:
                rc = subprocess.run(command, env=_build_env(global_env, plan["env"])).returncode
            except OSError as exc:
                print(f"error: step {idx} cannot run {command[0]!r}: {exc}", file=sys.stderr)
                rc = 127
            if rc != 0:
                if plan["continue"]:
                    print(f"warning: step {idx} failed (continue-on-error)", file=sys.stderr)
                    continue
                print(f"error: step {idx} failed with exit code {rc}", file=sys.stderr)
                return rc
        elif not _check_assertion(idx, plan):
            if plan["continue"]:
                print(f"warning: step {idx} failed (continue-on-error)", file=sys.stderr)
                continue
            return 1

    if dry_run:
        print("\n# dry-run complete")
    else:
        print("\npipeline complete")
    return 0
