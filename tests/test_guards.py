from pathlib import Path

import pytest

from ghostmode.errors import GuardViolation
from ghostmode.guards import ensure_loopback, ensure_safe_path, prepare_test_command


def test_rejects_external_host() -> None:
    with pytest.raises(GuardViolation):
        ensure_loopback("http://example.com")


def test_rejects_test_file(tmp_path: Path) -> None:
    path = tmp_path / "test_app.py"
    path.write_text("")
    with pytest.raises(GuardViolation):
        ensure_safe_path(path, tmp_path)


def test_uses_single_run_mode_for_vitest(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"scripts": {"test": "vitest"}}')
    assert prepare_test_command("npm test", tmp_path) == "npm test -- --run"
    assert prepare_test_command("npm run test", tmp_path) == "npm run test -- --run"


def test_keeps_other_test_commands_unchanged(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"scripts": {"test": "jest"}}')
    assert prepare_test_command("npm test", tmp_path) == "npm test"
    assert prepare_test_command("python -m pytest -q", tmp_path) == "python -m pytest -q"
