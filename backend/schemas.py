import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProfileInput(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=30)
    email: str | None = Field(default=None, max_length=254)
    address: str | None = Field(default=None, max_length=1000)

    @field_validator("name", "address")
    @classmethod
    def not_blank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("This field cannot be blank")
        return value.strip() if value else value

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        if value == "":
            return None
        if value is not None and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Enter a valid email address")
        return value

    @field_validator("phone")
    @classmethod
    def valid_phone(cls, value):
        if value is not None and not 7 <= len(re.sub(r"\D", "", value)) <= 15:
            raise ValueError("Phone must contain 7–15 digits")
        return value


class ChatInput(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    request_id: UUID
    image: str | None = Field(default=None, max_length=4_200_000)


class VoiceInput(BaseModel):
    audio: str = Field(min_length=20, max_length=25_000_000)


class ImageInput(BaseModel):
    image_url: str | None = Field(default=None, max_length=2000)
    alt_text: str = Field(default="Product image", min_length=1, max_length=300)


class DraftInput(BaseModel):
    product_id: str = Field(min_length=1, max_length=100)
    quantity: int = Field(ge=1, le=1000, strict=True)


class CheckoutConfirm(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=30)
    address: str | None = Field(default=None, max_length=1000)

    @field_validator("name")
    @classmethod
    def full_name(cls, value):
        if value is not None and not value.strip():
            raise ValueError("Enter your full name")
        return value.strip() if value else value

    @field_validator("address")
    @classmethod
    def full_address(cls, value):
        if value is not None and len(value.strip()) < 12:
            raise ValueError("Enter a full delivery address")
        return value.strip() if value else value

    @field_validator("phone")
    @classmethod
    def valid_phone(cls, value):
        if value is not None and not 7 <= len(re.sub(r"\D", "", value)) <= 15:
            raise ValueError("Phone must contain 7-15 digits")
        return value


class AdminLogin(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=72)


class ProductInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(max_length=100)
    price: float = Field(ge=0, allow_inf_nan=False)
    discounted_price: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    stock_count: int = Field(ge=0, le=1_000_000)
    colors: list[str] = Field(default_factory=list, max_length=30)
    sizes: list[str] = Field(default_factory=list, max_length=30)
    description: str = Field(default="", max_length=5000)


class PolicyInput(BaseModel):
    policy_type: str = Field(min_length=1, max_length=200)
    policy_text: str = Field(min_length=1, max_length=20000)


class ReplyInput(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class HandoffInput(BaseModel):
    enabled: bool
    note: str = Field(default="", max_length=2000)


class OrderStatusInput(BaseModel):
    status: Literal["Pending", "Processing", "Shipped", "Delivered", "Cancelled"]


class AdminOperation(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class CaseUpdate(AdminOperation):
    assigned_admin_id: int | None = None
    priority: Literal["normal", "urgent"] | None = None
    note: str | None = Field(default=None, min_length=1, max_length=4000)
    reason: str = Field(min_length=3, max_length=500)


class CaseResolution(AdminOperation):
    note: str = Field(default="Resolved by support", min_length=3, max_length=2000)
    resolution_code: Literal["resolved", "information_provided", "referred_to_merchant", "duplicate"] = "resolved"


class KnowledgeInput(AdminOperation):
    title: str = Field(min_length=3, max_length=200)
    category: str = Field(default="faq", max_length=100)
    locale: Literal["en", "bn", "bn-Latn"] = "en"
    content: str = Field(min_length=10, max_length=100000)
    reason: str = Field(min_length=3, max_length=500)


class KnowledgePublish(AdminOperation):
    version: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=500)


class AliasInput(AdminOperation):
    alias: str = Field(min_length=2, max_length=150)
    locale: Literal["en", "bn", "bn-Latn"] = "bn-Latn"


class RetrievalPreview(AdminOperation):
    query: str = Field(min_length=2, max_length=500)
    kind: Literal["policy", "product"] = "policy"
