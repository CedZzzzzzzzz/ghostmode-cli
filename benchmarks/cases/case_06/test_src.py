from src import safe_get


def test_missing_value_is_zero() -> None:
    assert safe_get({}, "missing") == 0
