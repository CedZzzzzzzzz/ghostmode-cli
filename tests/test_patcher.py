from pathlib import Path

from ghostmode.parser import FunctionSpan
from ghostmode.patcher import FilePatcher, extract_clean_code, matches_target_function, validate_code


def test_function_patch_and_rollback(tmp_path: Path) -> None:
    path = tmp_path / "app.py"
    original = "before = 1\ndef bad():\n    return 0\nafter = 2\n"
    path.write_text(original)
    patcher = FilePatcher()
    patcher.backup(path)
    patcher.apply_function_patch(path, FunctionSpan("def bad():\n    return 0\n", 2, 3, "bad"), "def bad():\n    return 1")
    assert "return 1" in path.read_text()
    patcher.rollback(path)
    assert path.read_text() == original


def test_extracts_fenced_code() -> None:
    assert extract_clean_code("hello\n```python\nx = 1\n```") == "x = 1\n"


def test_validates_supported_non_python_function() -> None:
    assert validate_code("export function total(x) { return x; }", ".ts")
    assert not validate_code("Here is the fix", ".ts")


def test_rejects_a_replacement_for_the_wrong_function() -> None:
    assert matches_target_function("def divide(a, b):\n    return a / b\n", "divide", ".py")
    assert not matches_target_function("def add(a, b):\n    return a - b\n", "divide", ".py")
    assert not matches_target_function("def divide(a, b):\n    return a / b\n\ndef add(a, b):\n    return a + b\n", "divide", ".py")
    assert not matches_target_function(
        "from datetime import date\n\ndef divide(a, b):\n    return a / b\n",
        "divide",
        ".py",
    )


def test_diff_marks_added_and_removed_lines() -> None:
    diff = FilePatcher().diff("value = 1\n", "value = 2\n")
    assert diff.plain == "--- before\n+++ after\n@@ -1 +1 @@\n-value = 1\n+value = 2\n"
    assert any(span.style == "red" for span in diff.spans)
    assert any(span.style == "green" for span in diff.spans)


def test_creates_a_new_valid_file(tmp_path: Path) -> None:
    path = tmp_path / "tests" / "test_regression.py"
    FilePatcher().apply_fix(path, "def test_regression():\n    assert True\n")
    assert path.read_text() == "def test_regression():\n    assert True\n"
