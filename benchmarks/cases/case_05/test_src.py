from src import discount


def test_discount() -> None:
    assert discount(100) == 90
