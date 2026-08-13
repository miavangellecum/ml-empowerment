# this is an issue cause of all he dif structure TODO: decide schema + requirments for DB
from pydantic import BaseModel, Field, model_validator
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
    items: List[LineItem] = Field(default_factory=list)
    subtotal: Optional[float] = Field(default=None, description="Total excluding VAT")
    vat_total: Optional[float] = Field(default=None, description="Total VAT/BTW amount")

    # Made optional (was `total: float`, required) because the Bedrock
    # structured-output call sometimes omits it on documents without an
    # obvious single "total due" line (e.g. utility bills paid by direct
    # debit) — and an omitted required field hard-crashes Pydantic
    # validation with no chance for us to intervene. We ask the model for
    # it in the prompt (extraction/llm/extract.py), but treat that as a
    # request, not a guarantee, and fall back to computing it ourselves.
    total: Optional[float] = Field(default=None, description="Final total amount paid")

    @model_validator(mode="after")
    def fill_missing_total(self):
        if self.total is not None:
            return self

        if self.subtotal is not None:
            self.total = self.subtotal + (self.vat_total or 0)
        elif self.items:
            self.total = sum(item.total_price for item in self.items)
        else:
            # Last resort: nothing to compute from. 0.0 keeps save_receipt's
            # NOT NULL `total` column happy; the record still gets an s3_url
            # and is visible in /receipts for manual correction, rather than
            # the whole upload being lost to a 500.
            self.total = 0.0

        return self