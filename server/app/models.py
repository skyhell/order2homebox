"""Domain models shared between scrapers, web UI and the Homebox client."""
from enum import Enum

from pydantic import BaseModel, Field

SHOP_DISPLAY_NAMES = {
    "amazon": "Amazon",
    "aliexpress": "AliExpress",
    "temu": "Temu",
    "banggood": "Banggood",
}


class Shop(str, Enum):
    amazon = "amazon"
    aliexpress = "aliexpress"
    temu = "temu"
    banggood = "banggood"

    @property
    def display_name(self) -> str:
        return SHOP_DISPLAY_NAMES[self.value]


class OrderItemDraft(BaseModel):
    """One order line as scraped; editable in the UI before Homebox creation."""

    name: str = ""
    description: str = ""
    quantity: int = 1
    unit_price: float | None = None
    currency: str = "EUR"
    product_url: str = ""
    image_url: str = ""


class Order(BaseModel):
    # A str, not Shop: a fetched order always carries one of the four shops
    # with a scraper, but a manual or hand-edited one can name any shop —
    # there is nothing to validate it against.
    shop: str
    order_no: str
    order_date: str = ""  # ISO date (YYYY-MM-DD) if known
    items: list[OrderItemDraft] = Field(default_factory=list)

    @property
    def shop_display_name(self) -> str:
        """One of the four known shops gets its proper-case name; anything
        else is already exactly what the user typed."""
        return SHOP_DISPLAY_NAMES.get(self.shop, self.shop)
