from typing import List, Optional

from pydantic import BaseModel, Field


class RestaurantContext(BaseModel):
    customer_name: str
    table_number: Optional[int] = None
    party_size: Optional[int] = 1
    current_order: List[str] = Field(default_factory=list)
    dietary_restrictions: Optional[str] = None


class RestaurantInputGuardrailOutput(BaseModel):
    is_off_topic: bool
    is_inappropriate: bool
    reason: str


class RestaurantHandoffData(BaseModel):
    to_agent_name: str
    request_type: str
    request_description: str
    reason: str


class RestaurantOutputGuardrailOutput(BaseModel):
    is_inappropriate: bool
    leaked_internal_info: bool
    reason: str
