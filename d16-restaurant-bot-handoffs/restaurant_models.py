from pydantic import BaseModel
from typing import Optional, List


class RestaurantContext(BaseModel):

    customer_name: str
    table_number: Optional[int] = None
    party_size: Optional[int] = 1
    current_order: List[str] = []
    dietary_restrictions: Optional[str] = None


class RestaurantInputGuardrailOutput(BaseModel):

    is_off_topic: bool
    reason: str


class RestaurantHandoffData(BaseModel):

    to_agent_name: str
    request_type: str
    request_description: str
    reason: str
