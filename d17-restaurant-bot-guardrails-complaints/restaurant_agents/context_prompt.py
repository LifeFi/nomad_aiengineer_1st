from models import RestaurantContext


def build_customer_context_block(context: RestaurantContext) -> str:
    restrictions = context.dietary_restrictions or "없음"
    return f"""
고객 고정 컨텍스트:
- 고객명: {context.customer_name}
- 인원수: {context.party_size}명
- 식이 제한: {restrictions}

중요:
- 위 정보는 사이드바에서 이미 확인된 고객 정보입니다.
- 사용자가 이번 메시지에서 다시 말하지 않아도 항상 알고 있는 정보로 취급하세요.
- 답변, 추천, 확인 질문, 해결책 제안 시 인원수와 식이 제한을 우선 반영하세요.
""".strip()
