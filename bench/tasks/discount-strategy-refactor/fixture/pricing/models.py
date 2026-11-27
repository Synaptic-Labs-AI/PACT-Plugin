"""Cart and order value objects.

All money is integer cents so the arithmetic stays exact.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CartItem:
    sku: str
    unit_price_cents: int
    quantity: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.sku, str) or not self.sku:
            raise ValueError("sku must be a non-empty string")
        if (
            isinstance(self.unit_price_cents, bool)
            or not isinstance(self.unit_price_cents, int)
            or self.unit_price_cents < 0
        ):
            raise ValueError("unit_price_cents must be a non-negative integer")
        if (
            isinstance(self.quantity, bool)
            or not isinstance(self.quantity, int)
            or self.quantity < 1
        ):
            raise ValueError("quantity must be a positive integer")

    @property
    def total_cents(self) -> int:
        return self.unit_price_cents * self.quantity


@dataclass(frozen=True)
class Order:
    items: tuple[CartItem, ...]
    customer_tier: str = "standard"
    coupon: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.customer_tier, str) or not self.customer_tier:
            raise ValueError("customer_tier must be a non-empty string")
        if self.coupon is not None and not isinstance(self.coupon, str):
            raise ValueError("coupon must be a string or None")
        try:
            resolved_items = tuple(self.items)
        except TypeError as exc:
            raise ValueError("items must be a sequence of CartItem") from exc
        for item in resolved_items:
            if not isinstance(item, CartItem):
                raise ValueError("items must be a sequence of CartItem")
        object.__setattr__(self, "items", resolved_items)

    @property
    def subtotal_cents(self) -> int:
        return sum(item.total_cents for item in self.items)

    @property
    def total_units(self) -> int:
        return sum(item.quantity for item in self.items)
