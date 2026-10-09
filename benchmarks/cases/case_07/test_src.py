from src import repeat


def test_repeat() -> None:
    assert repeat("x", 3) == "xxx"
