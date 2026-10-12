import json
import subprocess
from importlib import metadata

import pytest

import locallab
from locallab import cli, load_tools

TOOLS = load_tools()


def make_all(fake_tools, skip=(), **overrides):
    for name in TOOLS:
        if name not in skip:
            fake_tools(name, **overrides.get(name, {}))


# --- doctor / versions ------------------------------------------------------

def test_doctor_and_versions_all_ok(fake_tools, capsys):
    make_all(fake_tools)
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert out.count("PASS") == len(TOOLS)
    assert "All tools healthy." in out
    assert cli.main(["versions"]) == 0
    out = capsys.readouterr().out
    assert out.count("0.4.0") == len(TOOLS)
    assert "missing" not in out and "broken" not in out


def test_missing_tool_is_skipped_unless_strict(fake_tools, capsys):
    make_all(fake_tools, skip=("toolcall-cache",))
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "toolcall-cache --help ... SKIP (missing)" in out
    assert cli.main(["doctor", "--strict"]) == 1
    capsys.readouterr()
    assert cli.main(["versions"]) == 0
    assert any(
        line.split() == ["toolcall-cache", "missing"] for line in capsys.readouterr().out.splitlines()
    )


def test_broken_tool_fails(fake_tools, capsys):
    make_all(fake_tools, **{"agent-vcr": {"broken": True}})
    assert cli.main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "agent-vcr --help ... FAIL" in out
    assert "boom: broken install" in out
    assert cli.main(["versions"]) == 0
    assert any(line.split() == ["agent-vcr", "broken"] for line in capsys.readouterr().out.splitlines())


def test_versions_strips_v_prefix(fake_tools, capsys):
    make_all(fake_tools, **{"agent-vcr": {"version": "v0.5.1"}})
    assert cli.main(["versions"]) == 0
    assert any(line.split() == ["agent-vcr", "0.5.1"] for line in capsys.readouterr().out.splitlines())


def test_doctor_json(fake_tools, capsys):
    make_all(fake_tools, skip=("tokenauditor",), **{"agent-vcr": {"broken": True}})
    assert cli.main(["doctor", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False and data["strict"] is False
    assert [t["name"] for t in data["tools"]] == list(TOOLS)
    for tool in data["tools"]:
        assert set(tool) == {"name", "package", "status", "detail"}
        assert isinstance(tool["detail"], str)
    status = {t["name"]: t["status"] for t in data["tools"]}
    assert status["agent-vcr"] == "broken"
    assert status["tokenauditor"] == "missing"
    assert status["toolcall-linter"] == "ok"


def test_doctor_json_strict_missing(fake_tools, capsys):
    make_all(fake_tools, skip=("tokenauditor",))
    assert cli.main(["doctor", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert cli.main(["doctor", "--json", "--strict"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False and data["strict"] is True


@pytest.mark.parametrize(
    "text, expected",
    [
        ("agent-vcr v0.4.0", "0.4.0"),
        ("tool 0.4", "0.4"),
        ("0.4.0", "0.4.0"),
        ("V2.0.1", "2.0.1"),
        ("tool 1.2.3rc1", "1.2.3rc1"),
        ("tool (0.4.0)", "0.4.0"),
        ("no version here", ""),
    ],
)
def test_version_from_text(text, expected):
    assert cli._version_from_text(text) == expected


# --- install-all / update ---------------------------------------------------

@pytest.fixture
def pipx(monkeypatch):
    """Record pipx calls; `responses` maps an argv tuple to (rc, stdout, stderr)."""
    calls = []
    responses = {("list", "--json"): (0, json.dumps({"venvs": {}}), "")}

    def fake(args, check=False):
        calls.append(list(args))
        rc, out, err = responses.get(tuple(args), (0, "", ""))
        return subprocess.CompletedProcess(["pipx", *args], rc, out, err)

    monkeypatch.setattr(cli, "_pipx_run", fake)
    monkeypatch.setattr(cli, "_run_tool", lambda name, extra: (1, "", ""))
    return calls, responses


def test_install_all_from_pypi(pipx):
    calls, _ = pipx
    assert cli.main(["install-all"]) == 0
    assert calls[0] == ["list", "--json"]
    assert calls[1:] == [["install", meta["package"]] for meta in TOOLS.values()]
    assert ["install", "localab-checkpoint"] in calls


def test_install_all_from_git(pipx):
    calls, _ = pipx
    assert cli.main(["install-all", "--git"]) == 0
    assert ["install", "git+https://github.com/Victorchatter/AgentVCR.git"] in calls
    assert all(c[1].startswith("git+https://") for c in calls[1:])


def test_install_all_upgrades_already_installed(pipx, capsys):
    calls, responses = pipx
    responses[("list", "--json")] = (0, json.dumps({"venvs": {"localab-vcr": {}}}), "")
    responses[("upgrade", "localab-vcr")] = (1, "", "localab-vcr is already at latest version 0.4.0")
    responses[("install", "tokenauditor")] = (1, "", "'tokenauditor' already seems to be installed.")
    assert cli.main(["install-all"]) == 0
    assert ["upgrade", "localab-vcr"] in calls
    assert ["install", "localab-vcr"] not in calls
    assert "already installed, upgraded" in capsys.readouterr().out


def test_install_all_git_reinstalls_installed_tool_from_git(pipx, capsys):
    calls, responses = pipx
    responses[("list", "--json")] = (0, json.dumps({"venvs": {"localab-vcr": {}}}), "")
    assert cli.main(["install-all", "--git"]) == 0
    assert ["install", "--force", "git+https://github.com/Victorchatter/AgentVCR.git"] in calls
    assert not any(c[0] == "upgrade" for c in calls)
    assert "already installed, upgraded" in capsys.readouterr().out


def test_install_all_bad_pipx_list_json(pipx):
    calls, responses = pipx
    responses[("list", "--json")] = (1, "not json", "")
    assert cli.main(["install-all"]) == 0
    assert len([c for c in calls if c[0] == "install"]) == len(TOOLS)


def test_install_all_reports_failures(pipx, capsys):
    _, responses = pipx
    responses[("install", "toolcall-cache")] = (1, "", "no matching distribution")
    assert cli.main(["install-all"]) == 1
    assert "toolcall-cache: FAILED" in capsys.readouterr().out


@pytest.mark.parametrize("command", ["install-all", "update"])
def test_pipx_missing(command, monkeypatch, capsys):
    def no_pipx(*args, **kwargs):
        raise FileNotFoundError("pipx")

    monkeypatch.setattr(cli.subprocess, "run", no_pipx)
    assert cli.main([command]) == 1
    err = capsys.readouterr().err
    assert "error: pipx not found on PATH. Install pipx first: https://pipx.pypa.io" in err
    assert "Traceback" not in err


# --- init -------------------------------------------------------------------

INIT_FILES = [".locallab/tools.json", ".locallab/recipe.yaml", ".github/workflows/locallab.yml"]


def test_init_creates_and_skips(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init"]) == 0
    out = capsys.readouterr().out
    for rel in INIT_FILES:
        assert f"create {rel}" in out
        assert (tmp_path / rel).is_file()
    assert json.loads((tmp_path / ".locallab/tools.json").read_text()) == []
    recipe = (tmp_path / ".locallab/recipe.yaml").read_text()
    assert '--tools "{recipe_dir}/tools.json"' in recipe
    workflow = (tmp_path / ".github/workflows/locallab.yml").read_text()
    for step in ("pipx install locallab", "locallab install-all", "locallab doctor --strict",
                 '--dry-run --var agent_cmd="echo replace-me"', 'python-version: "3.12"'):
        assert step in workflow

    (tmp_path / ".locallab/tools.json").write_text('[{"name": "mine"}]')
    assert cli.main(["init"]) == 0
    out = capsys.readouterr().out
    for rel in INIT_FILES:
        assert f"skip {rel} (exists; use --force)" in out
    assert "mine" in (tmp_path / ".locallab/tools.json").read_text()

    assert cli.main(["init", "--force"]) == 0
    assert "create .locallab/tools.json" in capsys.readouterr().out
    assert json.loads((tmp_path / ".locallab/tools.json").read_text()) == []


def test_init_recipe_dry_runs(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    cli.main(["init"])
    capsys.readouterr()
    assert cli.main(["pipeline", ".locallab/recipe.yaml", "--dry-run", "--var", "agent_cmd=echo replace-me"]) == 0
    out = capsys.readouterr().out
    assert f"{(tmp_path / '.locallab').resolve().as_posix()}/tools.json" in out


# --- version ----------------------------------------------------------------

def test_version_matches_metadata():
    try:
        expected = metadata.version("locallab")
    except metadata.PackageNotFoundError:
        pytest.skip("locallab is not installed")
    assert locallab.__version__ == expected
