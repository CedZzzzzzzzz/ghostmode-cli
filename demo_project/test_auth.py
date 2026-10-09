from auth import calculate_discount


def test_bronze_discount() -> None:
    assert calculate_discount(100, "bronze") == 95


def test_gold_discount() -> None:
    assert calculate_discount(100, "gold") == 85


def test_unknown_tier_has_no_discount() -> None:
    assert calculate_discount(100, "guest") == 100
