"""Self-check for the locallab umbrella CLI.

Tests `locallab versions` and `locallab doctor` offline by creating fake
installed tools on a temporary PATH and pointing the CLI at them.\n"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _run_locallab(args: list[str], env: dict[str, str], cwd: Path = ROOT) -> tuple[int, str, str]:
    cmd = [sys.executable, "-m", "locallab.cli", *args]
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout, result.stderr


def _write_fake_tool(bindir: Path, name: str, version: str, subcommand: str = "") -> None:
    """Create a fake CLI tool on PATH that answers --version, --help and `subcommand`.

    On Windows the file is `<name>.py` (found through PATHEXT, see _fake_env);
    elsewhere it is an executable, extensionless `<name>` with a shebang
    pointing at this interpreter, so no `python3` on PATH is needed.
    """
    script = bindir / (f"{name}.py" if os.name == "nt" else name)
    content = (
        f"#!{sys.executable}\n"
        "import sys\n"
        f"name = {name!r}\n"
        f"subcommand = {subcommand!r}\n"
        f"version = {version!r}\n"
        "if len(sys.argv) > 1 and sys.argv[1] == subcommand:\n"
        "    print(f'{name} {subcommand} ok')\n"
        "elif len(sys.argv) > 1 and sys.argv[1] == '--version':\n"
        "    print(f'{name} {version}')\n"
        "elif len(sys.argv) > 1 and sys.argv[1] == '--help':\n"
        "    print(f'usage: {name} [--version] [--help]')\n"
    )
    script.write_text(content, encoding="utf-8")
    if os.name != "nt":
        os.chmod(script, 0o755)


def _write_all_fake_tools(bindir: Path) -> None:
    for name in (
        "agent-vcr", "tokenauditor", "toolcall-linter", "transcript-to-test",
        "agent-circuit-breaker", "toolcall-cache", "agent-checkpoint", "mcp-openai-bridge",
    ):
        _write_fake_tool(bindir, name, "0.4.0")
    _write_fake_tool(bindir, "transcript-bridge", "0.4.0", "formats")
    _write_fake_tool(bindir, "prompt-portability-linter", "0.4.0", "rules")


def _fake_env(bindir: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
    # Point PYTHONPATH at the local package so `python -m locallab.cli` works.
    env["PYTHONPATH"] = str(ROOT)
    if os.name == "nt" and ".PY" not in env.get("PATHEXT", "").upper().split(";"):
        # shutil.which only finds `<name>.py` when .PY is in PATHEXT; stock
        # Windows (and CI runners) leave it out.
        env["PATHEXT"] = env.get("PATHEXT", ".COM;.EXE;.BAT;.CMD") + ";.PY"
    return env


def check_versions() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        bindir = Path(tmp) / "bin"
        bindir.mkdir()

        env = _fake_env(bindir)
        _write_all_fake_tools(bindir)

        rc, out, err = _run_locallab(["versions"], env)
        assert rc == 0, f"versions failed: {err}\n{out}"
        assert "0.4.0" in out, f"expected version in output:\n{out}"
        for bad in ("missing", "broken"):
            assert bad not in out, f"all tools should appear installed:\n{out}"


def check_doctor() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        bindir = Path(tmp) / "bin"
        bindir.mkdir()

        env = _fake_env(bindir)
        _write_all_fake_tools(bindir)

        rc, out, err = _run_locallab(["doctor"], env)
        assert rc == 0, f"doctor failed: {err}\n{out}"
        assert "PASS" in out, f"expected PASS in doctor output:\n{out}"
        assert "All tools healthy." in out, f"expected healthy summary:\n{out}"


def check_pipeline_dry_run() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        recipe_dir = Path(tmp)
        recipe = recipe_dir / "demo.yaml"
        recipe.write_text(
            "name: demo\n"
            "steps:\n"
            '  - run: "echo hello {timestamp}"\n'
            "  - assert:\n"
            '      file: "{output}/x.json"\n'
            "      jq: '.ok == true'\n",
            encoding="utf-8",
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT)

        rc, out, err = _run_locallab(["pipeline", str(recipe), "--dry-run"], env)
        assert rc == 0, f"pipeline dry-run failed: {err}\n{out}"
        assert "hello" in out, f"expected expanded command in dry-run output:\n{out}"
        assert "step" in out, f"expected step markers in dry-run output:\n{out}"


def check_pipeline_assertion() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        recipe_dir = Path(tmp)
        data = recipe_dir / "data.json"
        data.write_text('{"total_cost": 1.50}\n', encoding="utf-8")
        recipe = recipe_dir / "assert.yaml"
        recipe.write_text(
            "name: assert-demo\n"
            "steps:\n"
            "  - assert:\n"
            "      file: data.json\n"
            "      jq: '.total_cost <= 2.00'\n"
            "  - assert:\n"
            "      file: data.json\n"
            "      jq: '.total_cost > 5.00'\n",
            encoding="utf-8",
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT)

        # Real run: the default {output} dir lands in cwd, so run from tmp.
        rc, out, err = _run_locallab(["pipeline", str(recipe)], env, cwd=recipe_dir)
        assert rc == 1, f"expected failing assertion to return 1, got {rc}:\n{out}\n{err}"
        assert "ASSERTION FAILED" in err, f"expected ASSERTION FAILED:\n{err}"


def check_packaged_recipes_dry_run() -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    for recipe in sorted((ROOT / "locallab" / "recipes").glob("*.yaml")):
        rc, out, err = _run_locallab(["pipeline", str(recipe), "--dry-run"], env)
        assert rc == 2, f"{recipe.name}: expected unresolved {{agent_cmd}} to exit 2, got {rc}:\n{err}"
        assert "unresolved placeholder(s): {agent_cmd}" in err, f"{recipe.name}: missing error:\n{err}"
        args = ["pipeline", str(recipe), "--dry-run", "--var", "agent_cmd=echo"]
        rc, out, err = _run_locallab(args, env)
        assert rc == 0, f"{recipe.name}: dry-run failed ({rc}):\n{err}\n{out}"
        assert "dry-run complete" in out, f"{recipe.name}: incomplete dry-run:\n{out}"


REQUIRED_RECIPE_SECTIONS = [
    "# ",
    "## What you need",
    "## Copy-paste commands",
    "## Expected output",
    "## Why this matters",
]

RECIPE_FILES = [
    "cap-claude-code-spend.md",
    "cache-filesystem-mcp.md",
    "regression-test-from-tape.md",
    "portability-check-before-migration.md",
    "resume-long-run.md",
]


def check_cookbook() -> None:
    examples_dir = ROOT / "examples"
    assert examples_dir.is_dir(), f"examples/ directory not found at {examples_dir}"
    for name in RECIPE_FILES:
        path = examples_dir / name
        assert path.exists(), f"missing recipe: {path}"
        text = path.read_text(encoding="utf-8")
        for section in REQUIRED_RECIPE_SECTIONS:
            assert section in text, f"{path} missing required section marker: {section}"
        # Every recipe must contain at least one bash fenced block.
        assert "```bash" in text, f"{path} missing a bash code block"
    print("cookbook OK")


def main() -> int:
    check_versions()
    check_doctor()
    check_pipeline_dry_run()
    check_pipeline_assertion()
    check_packaged_recipes_dry_run()
    check_cookbook()
    print("locallab selfcheck OK")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print(f"selfcheck FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
