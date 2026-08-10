# this is an issue cause of all he dif stucuture TODO: decide schema + requirments for DB
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import date


class LineItem(BaseModel):
    name: str = Field(description="Name/description of the item or line entry")
    quantity: float = Field(default=1, description="Quantity purchased, default 1 if not specified")
    unit_price: Optional[float] = Field(default=None, description="Price per unit, if available")
    total_price: float = Field(description="Total price for this line item")
    vat_rate: Optional[str] = Field(default=None, description="VAT/BTW rate for this item, e.g. '9%', '21%', 'Geen'")
    category: str = Field(description="One of: groceries, dining, transport, household, delivery, other")


class Receipt(BaseModel):
    store_name: str = Field(description="Name of the store or company issuing the receipt")
    date: Optional[str] = Field(default=None, description="Date of purchase, format YYYY-MM-DD if determinable")
    invoice_number: Optional[str] = Field(default=None, description="Invoice or order number, if present")
    payment_method: Optional[str] = Field(default=None, description="e.g. iDEAL, cash, card")
    currency: str = Field(default="EUR")
    items: List[LineItem]
    subtotal: Optional[float] = Field(default=None, description="Total excluding VAT")
    vat_total: Optional[float] = Field(default=None, description="Total VAT/BTW amount")
    total: float = Field(description="Final total amount paid")