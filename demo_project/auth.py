"""Small intentionally broken demo module."""


def calculate_discount(price: float, tier: str) -> float:
    """Return a tier-specific discounted price."""
    discounts = {"bronze": 0.05, "silver": 0.10, "gold": 0.15}
    return price * (1 - discounts.get(tier, 0.20))
