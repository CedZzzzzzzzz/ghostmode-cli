from pathlib import Path

from ghostmode.parser import extract_enclosing_function, language_for_path, parse_trace, truncate_log


def test_parses_deepest_application_frame(tmp_path: Path) -> None:
    source = tmp_path / "app.py"
    source.write_text("def broken():\n    return values[1]\n")
    trace = f'File "{tmp_path / "test_app.py"}", line 4, in test_x\nFile "{source}", line 2, in broken\nIndexError: bad'
    info = parse_trace(trace)
    assert info is not None
    assert info.failed_file == str(source)
    assert info.error_type == "IndexError"
    span = extract_enclosing_function(str(source), 2)
    assert span is not None
    assert span.name == "broken"


def test_malformed_trace_is_safe() -> None:
    assert parse_trace("unstructured output") is None
    assert truncate_log("a\nb\nc", lines=2) == "b\nc"


def test_parses_javascript_and_extracts_function(tmp_path: Path) -> None:
    source = tmp_path / "price.ts"
    source.write_text("export function total(price: number) {\n  return price * 2;\n}\n")
    info = parse_trace(f"at total ({source}:2:10)\nTypeError: bad price")
    assert info is not None
    assert info.failed_file == str(source)
    assert language_for_path(source) == "TypeScript"
    span = extract_enclosing_function(str(source), 2)
    assert span is not None
    assert span.name == "total"


def test_ignores_windows_standard_library_frame(tmp_path: Path) -> None:
    source = tmp_path / "calculator.py"
    source.write_text("def divide(a, b):\n    return a / b\n")
    trace = (
        'File "C:\\Users\\CedZ\\AppData\\Local\\Programs\\Python\\Python313\\Lib\\importlib\\__init__.py", line 88, in import_module\n'
        f'File "{source}", line 2, in divide\n'
        "ZeroDivisionError: division by zero"
    )
    info = parse_trace(trace)
    assert info is not None
    assert info.failed_file == str(source)


def test_resolves_imported_function_from_test_assertion(tmp_path: Path) -> None:
    source = tmp_path / "calculator.py"
    source.write_text("def power(base, exponent):\n    return base ^ exponent\n")
    test_file = tmp_path / "test_calculator.py"
    test_file.write_text("from calculator import power\n\ndef test_power():\n    assert power(2, 3) == 8\n")
    trace = f'File "{test_file}", line 4, in test_power\nAssertionError'
    info = parse_trace(trace)
    assert info is not None
    assert info.failed_file == str(source)
    assert info.function_name == "power"
