"""Strict JSON request models shared by FastAPI binding and OpenAPI generation."""
import re
from datetime import date
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError


def iso_date(value: str) -> str:
    try:
        if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
            raise ValueError
        date.fromisoformat(value)
    except ValueError:
        raise PydanticCustomError('datetime', 'must use YYYY-MM-DD')
    return value


def not_blank(value: str) -> str:
    if not value.strip():
        raise PydanticCustomError('notblank', 'must not be blank')
    return value


DateString = Annotated[str, AfterValidator(iso_date), Field(json_schema_extra={'format': 'date'})]
Title = Annotated[str, Field(min_length=1, max_length=100, json_schema_extra={'pattern': r'\S'}), AfterValidator(not_blank)]
ProductID = Annotated[str, Field(min_length=1, max_length=64)]


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, validate_default=True)


class WishlistInput(Input):
    title: Title
    description: str = Field(default='', max_length=1000)
    product_ids: list[ProductID] = Field(max_length=100, json_schema_extra={'uniqueItems': True})
    version: int = Field(default=0, ge=0)

    @field_validator('product_ids')
    @classmethod
    def unique_products(cls, value):
        if len(set(value)) != len(value):
            raise PydanticCustomError('unique', 'products must be unique')
        return value


class TripItem(Input):
    product_id: ProductID
    day: int = Field(ge=1, le=30)
    notes: str = Field(default='', max_length=1000)


class TripInput(Input):
    title: Title
    start_date: DateString
    end_date: DateString
    adults: int = Field(ge=1, le=16)
    children: int = Field(default=0, ge=0, le=16)
    items: list[TripItem] = Field(max_length=100)
    notes: str = Field(default='', max_length=2000)
    version: int = Field(default=0, ge=0)


class ReviewInput(Input):
    author: Annotated[str, Field(min_length=1, max_length=80, json_schema_extra={'pattern': r'\S'}), AfterValidator(not_blank)]
    rating: int = Field(ge=1, le=5)
    content: Annotated[str, Field(min_length=1, max_length=2000, json_schema_extra={'pattern': r'\S'}), AfterValidator(not_blank)]
    travel_date: DateString
    version: int = Field(default=0, ge=0)


class QuoteInput(Input):
    start_date: DateString
    end_date: DateString
    adults: int = Field(ge=1, le=16)
    children: int = Field(default=0, ge=0, le=16)
    quantity: int = Field(ge=1, le=8)
