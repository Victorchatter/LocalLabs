import os
import sys

import pytest


def write_fake_tool(bindir, name, version="0.4.0", broken=False):
    """Write a fake CLI: prints `<name> <version>` for --version, exits 0 otherwise.

    Windows finds `<name>.py` through PATHEXT; elsewhere the extensionless file
    runs via its sys.executable shebang.
    """
    script = bindir / (f"{name}.py" if os.name == "nt" else name)
    lines = [f"#!{sys.executable}", "import sys"]
    if broken:
        lines += ["print('boom: broken install', file=sys.stderr)", "sys.exit(1)"]
    else:
        lines += ["if sys.argv[1:2] == ['--version']:", f"    print({f'{name} {version}'!r})"]
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if os.name != "nt":
        script.chmod(0o755)


@pytest.fixture
def fake_tools(tmp_path, monkeypatch):
    """Return make(name, version="0.4.0", broken=False); PATH holds only the fake bin dir."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    monkeypatch.setenv("PATH", str(bindir))
    if os.name == "nt":
        monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD;.PY")

    def make(name, version="0.4.0", broken=False):
        write_fake_tool(bindir, name, version, broken)

    return make
