from pathlib import Path

from ghostmode.inspect import inspect_project, inspect_source


def test_finds_python_request_and_query_risks(tmp_path: Path) -> None:
    source = (
        "start = date.fromisoformat(params.get('start'))\n"
        "query = select(func.count(User.id)).join(Event)\n"
    )
    findings = inspect_source(tmp_path / "analytics.py", source)
    assert len(findings) == 2
    assert findings[0].severity == "high"
    assert findings[0].priority == "P1"
    assert "fromisoformat" in findings[0].message
    assert "distinct" in findings[1].message


def test_finds_react_request_risks(tmp_path: Path) -> None:
    source = (
        "useEffect(() => {\n"
        "  fetch(previousRange.current)\n"
        "}, [])\n"
        "setLoading(true)\n"
        "fetch('/pages').then(setPages)\n"
    )
    findings = inspect_source(tmp_path / "Dashboard.tsx", source)
    messages = [finding.message for finding in findings]
    assert any(finding.impact for finding in findings)
    assert any("mutable ref" in message for message in messages)
    assert any("empty dependency" in message for message in messages)
    assert any("Loading is enabled" in message for message in messages)


def test_skips_dependencies_and_large_files(tmp_path: Path) -> None:
    dependency = tmp_path / "node_modules" / "broken.js"
    dependency.parent.mkdir()
    dependency.write_text("fetch(previous.current)")
    source = tmp_path / "app.ts"
    source.write_text("fetch(previous.current)")
    assert len(inspect_project(tmp_path)) == 1


def test_skips_test_directories(tmp_path: Path) -> None:
    test_file = tmp_path / "tests" / "test_app.py"
    test_file.parent.mkdir()
    test_file.write_text("fetch(previous.current)")
    assert inspect_project(tmp_path) == []


def test_finds_optional_date_and_ratio_risks(tmp_path: Path) -> None:
    source = (
        "def parse(start_date: str | None):\n"
        "    return date.fromisoformat(start_date)\n"
        "bounce_rate = bounced_sessions / page_views\n"
    )
    findings = inspect_source(tmp_path / "analytics.py", source)
    messages = [finding.message for finding in findings]
    assert any("optional date" in message for message in messages)
    assert any("bounce metric" in message for message in messages)


def test_finds_loading_and_duplicate_effect_risks(tmp_path: Path) -> None:
    source = (
        "useEffect(() => {\n"
        "  fetchOverview()\n"
        "}, [range])\n"
        "useEffect(() => {\n"
        "  fetchOverview()\n"
        "}, [])\n"
        "setLoading(true)\n"
        "fetchPages().then(setPages)\n"
    )
    findings = inspect_source(tmp_path / "Dashboard.tsx", source)
    messages = [finding.message for finding in findings]
    assert any("multiple effects" in message for message in messages)
    assert any("Loading is enabled" in message for message in messages)


def test_finds_api_field_mismatches(tmp_path: Path) -> None:
    backend = tmp_path / "backend" / "analytics.py"
    frontend = tmp_path / "frontend" / "OtherPages.tsx"
    backend.parent.mkdir()
    frontend.parent.mkdir()
    backend.write_text('return {"page_path": page.path}')
    frontend.write_text("pages.map((page) => page.path)")
    findings = inspect_project(tmp_path)
    assert len(findings) == 1
    assert "backend exposes page_path" in findings[0].message
