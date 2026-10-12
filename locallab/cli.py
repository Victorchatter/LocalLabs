"""LocalLab umbrella CLI."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__, load_tools
from . import pipeline as pipeline_mod


def _tool_command(tool_name: str, extra: list[str]) -> list[str]:
    """Return the argv list for invoking an installed tool.

    Use the full resolved path so the subprocess call works on Windows even
    when the executable relies on PATHEXT. Script files (.py, .bat, .cmd) need
    an interpreter wrapper on Windows.
    """
    resolved = shutil.which(tool_name)
    if not resolved:
        return [sys.executable, "-m", tool_name.replace("-", "_"), *extra]

    lower = resolved.lower()
    if os.name == "nt":
        if lower.endswith(".py") or lower.endswith(".pyw"):
            return [sys.executable, resolved, *extra]
        if lower.endswith(".bat") or lower.endswith(".cmd"):
            return ["cmd", "/c", resolved, *extra]
    return [resolved, *extra]


def _run_tool(tool_name: str, extra: list[str]) -> tuple[int, str, str]:
    cmd = _tool_command(tool_name, extra)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode, result.stdout, result.stderr
    except FileNotFoundError:
        return -1, "", f"{tool_name}: command not found"
    except subprocess.TimeoutExpired:
        return -2, "", f"{tool_name}: timed out"


def _pipx_run(args: list[str], *, check: bool = False) -> subprocess.CompletedProcess:
    cmd = ["pipx", *args]
    try:
        return subprocess.run(cmd, check=check, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError(
            "pipx not found on PATH. Install pipx first: https://pipx.pypa.io"
        ) from exc


def _pipx_installed() -> set[str]:
    """Return the venv names pipx already manages (empty on any parse failure)."""
    result = _pipx_run(["list", "--json"])
    try:
        return set(json.loads(result.stdout)["venvs"])
    except (ValueError, KeyError, TypeError):
        return set()


def cmd_install_all(args: argparse.Namespace) -> int:
    tools = load_tools()
    try:
        installed = _pipx_installed()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Installing all LocalLab tools via pipx (from {'GitHub' if args.git else 'PyPI'})...")
    failures = []
    for name, meta in tools.items():
        pkg = meta["package"]
        if pkg in installed:
            print(f"\n[{name}] pipx upgrade {pkg}")
            result = _pipx_run(["upgrade", pkg])
            output = (result.stdout + result.stderr).lower()
            if result.returncode == 0 or "already at latest" in output:
                print("  already installed, upgraded")
                continue
        else:
            spec = f"git+https://github.com/Victorchatter/{meta['repo']}.git" if args.git else pkg
            print(f"\n[{name}] pipx install {spec}")
            result = _pipx_run(["install", spec])
            output = (result.stdout + result.stderr).lower()
            if result.returncode == 0 or "already seems to be installed" in output:
                print("  installed")
                continue
        print(f"  failed: {result.stderr.strip() or result.stdout.strip()}", file=sys.stderr)
        failures.append(name)
    print("\nInstall summary:")
    for name, meta in tools.items():
        pkg = meta["package"]
        if name in failures:
            print(f"  {name}: FAILED")
            continue
        ver_rc, ver_out, ver_err = _run_tool(name, ["--version"])
        text = (ver_out + ver_err).strip()
        version = text.splitlines()[0] if ver_rc == 0 and text else "unknown"
        print(f"  {name} ({pkg}): {version}")
    if failures:
        print(f"\n{len(failures)} install(s) failed.", file=sys.stderr)
        return 1
    print("\nAll LocalLab tools installed.")
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    tools = load_tools()
    print("Upgrading all LocalLab tools via pipx...")
    failures = []
    for name, meta in tools.items():
        pkg = meta["package"]
        print(f"\n[{name}] pipx upgrade {pkg}")
        try:
            result = _pipx_run(["upgrade", pkg])
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if result.returncode != 0:
            # A common "success" case is "not installed" which still exits nonzero.
            msg = result.stderr.strip() or result.stdout.strip()
            if "not installed" in msg.lower():
                print(f"  skipped: {pkg} not installed")
                continue
            print(f"  failed: {msg}", file=sys.stderr)
            failures.append(name)
        else:
            print("  upgraded")
    if failures:
        print(f"\n{len(failures)} upgrade(s) failed.", file=sys.stderr)
        return 1
    print("\nAll LocalLab tools upgraded.")
    return 0


def cmd_versions(args: argparse.Namespace) -> int:
    tools = load_tools()
    widths = {"name": max(len(n) for n in tools), "status": 12}
    print(f"{'tool':<{widths['name']}}  {'version':<{widths['status']}}")
    print("-" * (widths["name"] + widths["status"] + 2))
    for name in tools:
        if shutil.which(name) is None:
            version = "missing"
        else:
            rc, out, err = _run_tool(name, ["--version"])
            text = (out + err).strip()
            if rc != 0 or not text:
                version = "broken"
            else:
                first = text.splitlines()[0]
                version = _version_from_text(first) or first
        print(f"{name:<{widths['name']}}  {version:<{widths['status']}}")
    return 0


_VERSION_RE = re.compile(r"^[vV]?(\d+(?:\.\d+)+[A-Za-z0-9.+-]*)$")


def _version_from_text(text: str) -> str:
    """Find a version token: 'agent-vcr v0.4.0' -> '0.4.0', 'tool 0.4' -> '0.4'."""
    for token in text.split():
        m = _VERSION_RE.match(token.strip(",;()"))
        if m:
            return m.group(1)
    return ""


def _doctor_check(name: str, meta: dict) -> dict:
    smoke = list(meta.get("smoke") or ["--help"])
    entry = {"name": name, "package": meta["package"], "smoke": smoke}
    if shutil.which(name) is None:
        return {**entry, "status": "missing", "detail": "not found on PATH"}
    rc, out, err = _run_tool(name, smoke)
    if rc == 0:
        return {**entry, "status": "ok", "detail": ""}
    text = err.strip() or out.strip()
    detail = text.splitlines()[0] if text else f"exit code {rc}"
    return {**entry, "status": "broken", "detail": detail}


def cmd_doctor(args: argparse.Namespace) -> int:
    results = [_doctor_check(name, meta) for name, meta in load_tools().items()]
    broken = [r["name"] for r in results if r["status"] == "broken"]
    missing = [r["name"] for r in results if r["status"] == "missing"]
    code = 1 if broken or (args.strict and missing) else 0

    if args.json:
        tools = [{k: r[k] for k in ("name", "package", "status", "detail")} for r in results]
        print(json.dumps({"ok": code == 0, "strict": args.strict, "tools": tools}, indent=2))
        return code

    labels = {"ok": "PASS", "missing": "SKIP (missing)", "broken": "FAIL"}
    print("LocalLab doctor")
    print("-" * 40)
    for r in results:
        print(f"  {r['name']} {' '.join(r['smoke'])} ... {labels[r['status']]}")
        if r["status"] == "broken":
            print(f"    {r['detail']}")
    print("-" * 40)
    if broken:
        print(f"Failures: {', '.join(broken)}", file=sys.stderr)
    if missing:
        print(
            f"Missing: {', '.join(missing)} (run `locallab install-all`)",
            file=sys.stderr if args.strict else sys.stdout,
        )
    if not broken and not missing:
        print("All tools healthy.")
    elif code == 0:
        print("No broken tools.")
    return code


def cmd_pipeline(args: argparse.Namespace) -> int:
    packaged = pipeline_mod.packaged_recipes()
    if args.list:
        for name, path in packaged.items():
            print(f"{name}  {path}")
        return 0
    if not args.recipe:
        args.pipe_parser.error("a recipe path or packaged recipe name is required (see --list)")
    recipe = args.recipe
    if not os.path.isfile(recipe) and recipe in packaged:
        recipe = str(packaged[recipe])
    return pipeline_mod.run(recipe, dry_run=args.dry_run, variables=dict(args.var))


def _parse_var(text: str) -> tuple[str, str]:
    key, sep, value = text.partition("=")
    if not sep or not key:
        raise argparse.ArgumentTypeError(f"expected KEY=VAL, got {text!r}")
    return key, value


INIT_TOOLS_JSON = "[]\n"

INIT_WORKFLOW = """\
name: locallab

on: [push, pull_request]

jobs:
  locallab:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pipx install locallab
      - run: locallab install-all
      - run: locallab doctor --strict
      - run: locallab pipeline .locallab/recipe.yaml --dry-run --var agent_cmd="echo replace-me"
"""


def _init_recipe() -> str:
    """The packaged record-audit-lint recipe, reading tool schemas next to itself."""
    text = (pipeline_mod.RECIPES_DIR / "record-audit-lint.yaml").read_text(encoding="utf-8")
    return text.replace("{input}/tools.json", "{recipe_dir}/tools.json")


def cmd_init(args: argparse.Namespace) -> int:
    files = {
        ".locallab/tools.json": INIT_TOOLS_JSON,
        ".locallab/recipe.yaml": _init_recipe(),
        ".github/workflows/locallab.yml": INIT_WORKFLOW,
    }
    for rel, content in files.items():
        path = Path.cwd() / rel
        if path.exists() and not args.force:
            print(f"skip {rel} (exists; use --force)")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        print(f"create {rel}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="locallab",
        description="Umbrella CLI for the LocalLab family of local-first agent tools.",
    )
    parser.add_argument(
        "--version", action="version", version=f"locallab {__version__}"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    install = sub.add_parser(
        "install-all",
        help="install all 10 LocalLab tools via pipx from PyPI (requires network)",
    )
    install.add_argument(
        "--git", action="store_true", help="install from the GitHub repos instead of PyPI"
    )
    install.set_defaults(func=cmd_install_all)

    sub.add_parser(
        "update",
        help="upgrade all installed LocalLab tools via pipx (requires network)",
    ).set_defaults(func=cmd_update)

    sub.add_parser(
        "versions",
        help="print installed versions of each tool (offline; uses --version)",
    ).set_defaults(func=cmd_versions)

    doctor = sub.add_parser(
        "doctor",
        help="smoke-test each installed tool with a deterministic command (offline)",
    )
    doctor.add_argument("--strict", action="store_true", help="also fail when a tool is missing")
    doctor.add_argument("--json", action="store_true", help="print one JSON object instead of a table")
    doctor.set_defaults(func=cmd_doctor)

    pipe = sub.add_parser(
        "pipeline",
        help="run a cross-tool pipeline recipe YAML (offline when recipes are local)",
    )
    pipe.add_argument(
        "recipe",
        nargs="?",
        help="path to recipe YAML, or the name of a packaged recipe (see --list)",
    )
    pipe.add_argument(
        "--dry-run",
        action="store_true",
        help="print resolved commands without executing",
    )
    pipe.add_argument(
        "--var",
        action="append",
        default=[],
        type=_parse_var,
        metavar="KEY=VAL",
        help="set a recipe placeholder, e.g. --var agent_cmd='python agent.py' (repeatable)",
    )
    pipe.add_argument("--list", action="store_true", help="list packaged recipes and exit")
    pipe.set_defaults(func=cmd_pipeline, pipe_parser=pipe)

    init = sub.add_parser(
        "init",
        help="scaffold .locallab/ and a GitHub Actions workflow in the current directory",
    )
    init.add_argument("--force", action="store_true", help="overwrite files that already exist")
    init.set_defaults(func=cmd_init)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
