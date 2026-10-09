from src import first


def test_first() -> None:
    assert first(["a", "b"]) == "a"
