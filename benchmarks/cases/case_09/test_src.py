from src import clamp


def test_clamp() -> None:
    assert clamp(12, 0, 10) == 10
