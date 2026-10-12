import sys
from pathlib import Path

import pytest
import yaml

from locallab import cli, pipeline

# Forward slashes keep the path intact through POSIX shlex.split on Windows.
PY = Path(sys.executable).as_posix()
EXIT = '"{py}" -c "import sys; sys.exit(int(sys.argv[1]))" '
WRITE_JSON = (
    '"{py}" -c "import sys, pathlib; pathlib.Path(sys.argv[1]).write_text(sys.argv[2])" '
    "\"{output}/x.json\" '{\"ok\": true}'"
)


def write_recipe(path, steps, variables=None):
    data = {"name": "t", "vars": {"py": PY, **(variables or {})}, "steps": steps}
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return str(path)


def output_dirs(root):
    return list(Path(root).glob("pipeline-output-*"))


@pytest.fixture
def cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_packaged_recipes_parse():
    recipes = pipeline.packaged_recipes()
    assert set(recipes) == {"hydrate-replay", "record-audit-lint"}
    for path in recipes.values():
        data = pipeline._load_recipe(str(path))
        assert isinstance(data["steps"], list) and data["steps"]


@pytest.mark.parametrize("name", ["hydrate-replay", "record-audit-lint"])
def test_packaged_recipe_needs_agent_cmd(name, cwd, capsys):
    path = str(pipeline.packaged_recipes()[name])
    assert cli.main(["pipeline", path, "--dry-run"]) == 2
    err = capsys.readouterr().err
    assert "unresolved placeholder(s): {agent_cmd}" in err
    assert "--var agent_cmd=..." in err
    assert cli.main(["pipeline", path, "--dry-run", "--var", "agent_cmd=python agent.py"]) == 0
    assert "python agent.py" in capsys.readouterr().out
    assert output_dirs(cwd) == []


def test_run_packaged_recipe_by_name(cwd, capsys):
    assert cli.main(["pipeline", "record-audit-lint", "--dry-run", "--var", "agent_cmd=echo"]) == 0
    assert "# dry-run: record-audit-lint" in capsys.readouterr().out


def test_unresolved_placeholder_real_run_runs_nothing(cwd, capsys):
    recipe = write_recipe(cwd / "r.yaml", [{"run": WRITE_JSON}, {"run": "{agent_cmd}"}])
    assert cli.main(["pipeline", recipe]) == 2
    assert "error: step 2: unresolved placeholder(s): {agent_cmd}" in capsys.readouterr().err
    assert output_dirs(cwd) == []


def test_var_resolves_placeholder(cwd):
    recipe = write_recipe(cwd / "r.yaml", [{"run": EXIT + "{code}"}])
    assert cli.main(["pipeline", recipe, "--var", "code=0"]) == 0
    assert cli.main(["pipeline", recipe, "--var", "code=4"]) == 4


def test_recipe_vars_default_and_override(cwd, capsys):
    recipe = write_recipe(
        cwd / "r.yaml",
        [{"run": "echo {greeting} {where}"}],
        {"greeting": "hi", "where": "{recipe_dir}/data"},
    )
    assert cli.main(["pipeline", recipe, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "echo hi " in out
    assert f"{cwd.resolve().as_posix()}/data" in out
    assert cli.main(["pipeline", recipe, "--dry-run", "--var", "greeting=yo"]) == 0
    assert "echo yo " in capsys.readouterr().out


def test_malformed_var_is_usage_error(cwd):
    with pytest.raises(SystemExit) as exc:
        cli.main(["pipeline", "record-audit-lint", "--var", "agent_cmd"])
    assert exc.value.code == 2


def test_output_dir_created_on_real_run_only(cwd):
    recipe = write_recipe(cwd / "r.yaml", [{"run": WRITE_JSON}])
    assert cli.main(["pipeline", recipe, "--dry-run"]) == 0
    assert output_dirs(cwd) == []
    assert cli.main(["pipeline", recipe]) == 0
    [out_dir] = output_dirs(cwd)
    assert (out_dir / "x.json").read_text() == '{"ok": true}'


def test_assert_exists_and_jq_pass(cwd):
    recipe = write_recipe(
        cwd / "r.yaml",
        [
            {"run": WRITE_JSON},
            {"assert": {"exists": "{output}/x.json"}},
            {"assert": {"file": "{output}/x.json", "jq": ".ok == true"}},
        ],
    )
    assert cli.main(["pipeline", recipe]) == 0


def test_assert_exists_fails(cwd, capsys):
    recipe = write_recipe(cwd / "r.yaml", [{"assert": {"exists": "{output}/nope.db"}}])
    assert cli.main(["pipeline", recipe]) == 1
    assert "ASSERTION FAILED: step 1 (exists" in capsys.readouterr().err


def test_assert_relative_path_uses_recipe_dir(tmp_path, monkeypatch):
    recipe_dir = tmp_path / "recipes"
    recipe_dir.mkdir()
    (recipe_dir / "data.json").write_text('{"total_cost": 1.5}', encoding="utf-8")
    recipe = write_recipe(recipe_dir / "r.yaml", [{"assert": {"file": "data.json", "jq": ".total_cost <= 2.00"}}])
    monkeypatch.chdir(tmp_path)
    assert cli.main(["pipeline", recipe]) == 0


def test_assert_dry_run_prints_exists(cwd, capsys):
    recipe = write_recipe(cwd / "r.yaml", [{"assert": {"exists": "{output}/cache.db"}}])
    assert cli.main(["pipeline", recipe, "--dry-run"]) == 0
    assert "# exists " in capsys.readouterr().out


def test_assert_bad_shape(cwd, capsys):
    recipe = write_recipe(cwd / "r.yaml", [{"assert": {"file": "x.json"}}])
    assert cli.main(["pipeline", recipe]) == 2
    assert "error: step 1 assert needs 'file'+'jq' or 'exists'" in capsys.readouterr().err


def test_jq_error_is_assertion_failure(cwd, capsys):
    recipe = write_recipe(
        cwd / "r.yaml",
        [{"run": WRITE_JSON}, {"assert": {"file": "{output}/x.json", "jq": ".ok.deeper"}}],
    )
    assert cli.main(["pipeline", recipe]) == 1
    err = capsys.readouterr().err
    assert "ASSERTION FAILED: step 2 (.ok.deeper) -> error:" in err
    assert "Traceback" not in err


def test_maybe_resolve_path(tmp_path):
    (tmp_path / "fs.v2").write_text("", encoding="utf-8")
    assert pipeline._maybe_resolve_path("fs.v2", str(tmp_path)) == "fs.v2"
    assert pipeline._maybe_resolve_path("--server-id", str(tmp_path)) == "--server-id"
    resolved = pipeline._maybe_resolve_path("./fs.v2", str(tmp_path))
    assert Path(resolved) == (tmp_path / "fs.v2").resolve()


def test_if_skips_step(cwd, capsys):
    recipe = write_recipe(cwd / "r.yaml", [{"run": EXIT + "3", "if": "{flag}"}], {"flag": "off"})
    assert cli.main(["pipeline", recipe]) == 0
    assert "[step 1] skipped (if: off)" in capsys.readouterr().err
    assert cli.main(["pipeline", recipe, "--dry-run"]) == 0
    assert "# step 1 skipped (if: off)" in capsys.readouterr().out
    assert cli.main(["pipeline", recipe, "--var", "flag=yes"]) == 3


def test_continue_on_error(cwd, capsys):
    recipe = write_recipe(
        cwd / "r.yaml",
        [
            {"run": EXIT + "3", "continue-on-error": True},
            {"assert": {"exists": "{output}/nope"}, "continue-on-error": True},
            {"run": EXIT + "0"},
        ],
    )
    assert cli.main(["pipeline", recipe]) == 0
    err = capsys.readouterr().err
    assert "warning: step 1 failed (continue-on-error)" in err
    assert "warning: step 2 failed (continue-on-error)" in err


def test_failing_step_stops_pipeline(cwd):
    recipe = write_recipe(cwd / "r.yaml", [{"run": EXIT + "5"}, {"run": WRITE_JSON}])
    assert cli.main(["pipeline", recipe]) == 5
    [out_dir] = output_dirs(cwd)
    assert not (out_dir / "x.json").exists()


def test_pipeline_list(capsys):
    assert cli.main(["pipeline", "--list"]) == 0
    lines = capsys.readouterr().out.splitlines()
    names = [line.split()[0] for line in lines]
    assert names == ["hydrate-replay", "record-audit-lint"]


def test_pipeline_needs_recipe_or_list():
    with pytest.raises(SystemExit) as exc:
        cli.main(["pipeline"])
    assert exc.value.code == 2


def test_yaml_parse_error(cwd, capsys):
    bad = cwd / "bad.yaml"
    bad.write_text("steps:\n  - assert:\n      file: {output}/x.json\n      jq: '.'\n", encoding="utf-8")
    assert cli.main(["pipeline", str(bad)]) == 2
    err = capsys.readouterr().err
    assert f"error: cannot parse recipe {bad}" in err
    assert "Traceback" not in err


def test_missing_recipe_file(cwd, capsys):
    assert cli.main(["pipeline", str(cwd / "nope.yaml")]) == 2
    assert "error: cannot parse recipe" in capsys.readouterr().err
