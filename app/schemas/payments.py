from pydantic import BaseModel, Field

from app.schemas.courses import OrderOut


class CheckoutRequest(BaseModel):
    payment_method: str = Field(pattern=r"^(card|paypal|mobile_money|cash)$")
    # Mobile money (module Flutterwave) — acceptés dès maintenant, utilisés plus tard
    operator: str | None = Field(default=None, max_length=40)
    phone_number: str | None = Field(default=None, max_length=20)


class CheckoutResponse(BaseModel):
    order: OrderOut
    redirect_url: str | None = None


class AdminOrderOut(OrderOut):
    user_id: str
    customer_name: str = ""
