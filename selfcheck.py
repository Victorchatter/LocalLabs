"""Self-check for the locallab umbrella CLI.

Tests `locallab versions` and `locallab doctor` offline by creating fake
installed tools on a temporary PATH and pointing the CLI at them.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _run_locallab(args: list[str], env: dict[str, str]) -> tuple[int, str, str]:
    cmd = [sys.executable, "-m", "locallab.cli", *args]
    result = subprocess.run(
        cmd,
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout, result.stderr


def _fake_tool_path(bindir: Path, name: str) -> Path:
    """Path for a fake tool script such that shutil.which(name) finds it.

    On Windows, PATHEXT resolution means shutil.which('agent-vcr') finds
    agent-vcr.py, so the .py extension is required there. On POSIX, PATHEXT
    resolution does not apply: shutil.which only matches the exact, executable
    filename, so the fake tool must be named plainly (no .py suffix).
    """
    return bindir / (f"{name}.py" if os.name == "nt" else name)


def _write_fake_tool(bindir: Path, name: str, version: str) -> None:
    """Create a fake CLI tool as a Python script on PATH."""
    script = _fake_tool_path(bindir, name)
    content = (
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"name = {name!r}\n"
        f"version = {version!r}\n"
        "if len(sys.argv) > 1 and sys.argv[1] == '--version':\n"
        "    print(f'{name} {version}')\n"
        "elif len(sys.argv) > 1 and sys.argv[1] == '--help':\n"
        "    print(f'usage: {name} [--version] [--help]')\n"
    )
    script.write_text(content, encoding="utf-8")
    if os.name != "nt":
        os.chmod(script, 0o755)


def _write_fake_tool_subcommand(bindir: Path, name: str, subcommand: str, version: str) -> None:
    script = _fake_tool_path(bindir, name)
    content = (
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"name = {name!r}\n"
        f"subcommand = {subcommand!r}\n"
        f"version = {version!r}\n"
        "if len(sys.argv) > 1 and sys.argv[1] == subcommand:\n"
        "    print(f'{name} {subcommand} ok')\n"
        "elif len(sys.argv) > 1 and sys.argv[1] == '--version':\n"
        "    print(f'{name} {version}')\n"
        "elif len(sys.argv) > 1 and sys.argv[1] == '--help':\n"
        "    print(f'usage: {name} [{subcommand}] [--version] [--help]')\n"
    )
    script.write_text(content, encoding="utf-8")
    if os.name != "nt":
        os.chmod(script, 0o755)


def check_versions() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        bindir = Path(tmp) / "bin"
        bindir.mkdir()

        _write_fake_tool(bindir, "agent-vcr", "0.4.0")
        _write_fake_tool(bindir, "tokenauditor", "0.4.0")
        _write_fake_tool(bindir, "toolcall-linter", "0.4.0")
        _write_fake_tool(bindir, "transcript-to-test", "0.4.0")
        _write_fake_tool(bindir, "agent-circuit-breaker", "0.4.0")
        _write_fake_tool(bindir, "toolcall-cache", "0.4.0")
        _write_fake_tool(bindir, "agent-checkpoint", "0.4.0")
        _write_fake_tool_subcommand(bindir, "transcript-bridge", "formats", "0.4.0")
        _write_fake_tool(bindir, "mcp-openai-bridge", "0.4.0")
        _write_fake_tool_subcommand(bindir, "prompt-portability-linter", "rules", "0.4.0")

        env = dict(os.environ)
        env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
        # Point PYTHONPATH at the local package so `python -m locallab.cli` works.
        env["PYTHONPATH"] = str(ROOT)

        rc, out, err = _run_locallab(["versions"], env)
        assert rc == 0, f"versions failed: {err}\n{out}"
        assert "0.4.0" in out, f"expected version in output:\n{out}"
        assert "not installed" not in out, f"all tools should appear installed:\n{out}"


def check_doctor() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        bindir = Path(tmp) / "bin"
        bindir.mkdir()

        _write_fake_tool(bindir, "agent-vcr", "0.4.0")
        _write_fake_tool(bindir, "tokenauditor", "0.4.0")
        _write_fake_tool(bindir, "toolcall-linter", "0.4.0")
        _write_fake_tool(bindir, "transcript-to-test", "0.4.0")
        _write_fake_tool(bindir, "agent-circuit-breaker", "0.4.0")
        _write_fake_tool(bindir, "toolcall-cache", "0.4.0")
        _write_fake_tool(bindir, "agent-checkpoint", "0.4.0")
        _write_fake_tool_subcommand(bindir, "transcript-bridge", "formats", "0.4.0")
        _write_fake_tool(bindir, "mcp-openai-bridge", "0.4.0")
        _write_fake_tool_subcommand(bindir, "prompt-portability-linter", "rules", "0.4.0")

        env = dict(os.environ)
        env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
        env["PYTHONPATH"] = str(ROOT)

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

        rc, out, err = _run_locallab(["pipeline", str(recipe)], env)
        assert rc == 1, f"expected failing assertion to return 1, got {rc}:\n{out}\n{err}"
        assert "ASSERTION FAILED" in err, f"expected ASSERTION FAILED:\n{err}"


def check_pipeline_output_dir_created() -> None:
    """A step that writes into {output} before any other step creates it must
    succeed: run() must mkdir the output directory itself rather than assuming
    a prior step or the wrapped tool will."""
    with tempfile.TemporaryDirectory() as tmp:
        recipe_dir = Path(tmp)
        recipe = recipe_dir / "writes-output.yaml"
        recipe.write_text(
            "name: writes-output\n"
            "steps:\n"
            "  - run: \"sh -c 'echo hi > {output}/marker.txt'\"\n"
            "  - assert:\n"
            "      file: \"{output}/marker.txt\"\n"
            "      exists: true\n",
            encoding="utf-8",
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT)

        rc, out, err = _run_locallab(["pipeline", str(recipe)], env)
        assert rc == 0, f"expected output dir to exist before step 1 ran: {err}\n{out}"
        assert "assertion passed" in err, f"expected exists assertion to pass:\n{err}"


def check_pipeline_unresolved_placeholder() -> None:
    """A recipe referencing an undefined placeholder (e.g. {agent_cmd}, which
    no recipe or CLI flag defines) must fail loudly on a real run instead of
    silently passing the literal '{agent_cmd}' text to a subprocess."""
    with tempfile.TemporaryDirectory() as tmp:
        recipe_dir = Path(tmp)
        recipe = recipe_dir / "undefined-placeholder.yaml"
        recipe.write_text(
            "name: undefined-placeholder\n"
            "steps:\n"
            "  - run: \"echo {agent_cmd}\"\n",
            encoding="utf-8",
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT)

        rc, out, err = _run_locallab(["pipeline", str(recipe)], env)
        assert rc == 2, f"expected unresolved placeholder to fail fast, got {rc}:\n{out}\n{err}"
        assert "unresolved placeholder" in err, f"expected a clear error naming the placeholder:\n{err}"

        rc, out, err = _run_locallab(["pipeline", str(recipe), "--dry-run"], env)
        assert rc == 0, f"dry-run should still preview a recipe with unresolved placeholders: {err}\n{out}"
        assert "WARNING" in out, f"expected a dry-run warning about the unresolved placeholder:\n{out}"


def check_shipped_recipes() -> None:
    """Every recipe in locallab/recipes/ must be valid, parseable, and dry-runnable.

    Regression test for a bug where unquoted {output}/{input} placeholders at
    the start of a YAML scalar (e.g. `file: {output}/audit.json`) were parsed
    as YAML flow-mapping syntax and broke `locallab pipeline` immediately.
    """
    recipes_dir = ROOT / "locallab" / "recipes"
    assert recipes_dir.is_dir(), f"recipes/ directory not found at {recipes_dir}"
    recipe_files = sorted(recipes_dir.glob("*.yaml"))
    assert recipe_files, f"no recipe files found in {recipes_dir}"

    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)

    for recipe in recipe_files:
        rc, out, err = _run_locallab(["pipeline", str(recipe), "--dry-run"], env)
        assert rc == 0, f"{recipe.name} failed to dry-run: {err}\n{out}"
    print("shipped recipes OK")


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
    check_pipeline_output_dir_created()
    check_pipeline_unresolved_placeholder()
    check_shipped_recipes()
    check_cookbook()
    print("locallab selfcheck OK")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print(f"selfcheck FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
