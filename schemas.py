"""
Database Schemas for the demo app

Each Pydantic model maps to a MongoDB collection named by the lowercase
of the class name (e.g., Product -> "product").
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from datetime import datetime

VoteOption = Literal["auction", "buy_now", "tokenization", "raffle", "not_interested"]


class ProductLocale(BaseModel):
    locale: Literal["ro", "it", "en"]
    title: str
    description: Optional[str] = None


class Product(BaseModel):
    
    # Content
    locales: List[ProductLocale] = Field(
        ..., description="Localized content for the product"
    )
    category: Optional[str] = None
    images: List[str] = Field(default_factory=list)

    # Voting window
    vote_start_at: datetime
    vote_end_at: datetime

    # Pricing options
    auction_start_price: Optional[float] = Field(None, ge=0)
    buy_now_price: Optional[float] = Field(None, ge=0)
    shares_total: Optional[int] = Field(None, ge=0)
    share_price: Optional[float] = Field(None, ge=0)
    raffle_tickets_total: Optional[int] = Field(None, ge=0)
    raffle_ticket_price: Optional[float] = Field(None, ge=0)

    status: str = Field(
        "in_voting",
        description="Draft, in_voting, vote_expired, auction, buy_now, tokenization, raffle, sold, rejected",
    )


class Vote(BaseModel):
    product_id: str
    user_id: str
    option: VoteOption
    desired_shares: Optional[int] = Field(None, ge=1)
    desired_tickets: Optional[int] = Field(None, ge=1)


class User(BaseModel):
    email: str
    name: Optional[str] = None
    role: Literal["admin", "operator", "user"] = "user"
    locale: Literal["ro", "it", "en"] = "ro"
    is_active: bool = True
