import streamlit as st
from agents import (
    Agent,
    handoff,
    RunContextWrapper,
)
from agents.extensions.handoff_prompt import RECOMMENDED_PROMPT_PREFIX
from agents.extensions import handoff_filters
from models import RestaurantContext, RestaurantHandoffData
from restaurant_agents.complaints_agent import complaints_agent
from restaurant_agents.guardrails import (
    restaurant_guardrail,
    restaurant_output_guardrail,
)
from restaurant_agents.menu_agent import menu_agent
from restaurant_agents.order_agent import order_agent
from restaurant_agents.reservation_agent import reservation_agent


def dynamic_triage_agent_instructions(
    wrapper: RunContextWrapper[RestaurantContext],
    agent: Agent[RestaurantContext],
):
    restrictions = wrapper.context.dietary_restrictions or "없음"

    return f"""
    {RECOMMENDED_PROMPT_PREFIX}

    당신은 레스토랑의 안내 직원입니다. 항상 한국어로 친절하게 응대하세요.
    고객 {wrapper.context.customer_name}님을 환영합니다!

    고객 정보:
    - 인원: {wrapper.context.party_size}명
    - 식이 제한: {restrictions}

    중요:
    - 위 고객 정보는 현재 대화의 고정 컨텍스트이므로 항상 우선 참고하세요.
    - 이름뿐 아니라 인원수와 식이 제한도 함께 인식하고 판단에 반영하세요.
    - 사용자의 메시지에 해당 정보가 다시 나오지 않아도 이미 제공된 사실로 간주하세요.

    당신의 역할: 고객의 요청을 파악하고 적절한 전담 직원에게 연결합니다.

    요청 분류 기준:

    🍽️ 메뉴 안내 (Menu Agent로 연결):
    - 메뉴 추천, 음식 설명, 재료 문의
    - 알레르기 및 식이 제한 관련 질문
    - 오늘의 특선, 인기 메뉴 문의
    - 가격 문의
    예시: "채식 메뉴가 있나요?", "스테이크 재료가 뭔가요?", "견과류 알레르기가 있어요"

    🛒 주문 접수 (Order Agent로 연결):
    - 음식 주문, 추가 주문
    - 주문 변경 또는 취소
    - 특별 요청 사항 (소스 따로, 재료 제외 등)
    예시: "스테이크 주문할게요", "음료 추가해주세요", "주문 변경하고 싶어요"

    📅 예약 (Reservation Agent로 연결):
    - 테이블 예약, 예약 변경, 예약 취소
    - 단체 예약, 특별 행사 예약
    - 프라이빗 룸 예약
    예시: "내일 저녁 예약하고 싶어요", "4명 예약 가능한가요?", "생일 파티 예약"

    😟 불만/클레임 처리 (Complaints Agent로 연결):
    - 음식 품질 불만, 서비스 지연, 직원 응대 불만
    - 잘못된 주문, 환불/할인 요청, 매니저 호출 요청
    - 위생/안전/알레르기 사고 관련 문제 제기
    예시: "음식이 너무 늦어요", "주문이 잘못 나왔어요", "환불 받고 싶어요"

    처리 절차:
    1. 고객의 요청을 주의 깊게 듣습니다.
    2. 요청이 불명확하면 1~2가지 질문으로 명확히 합니다.
    3. 적절한 담당자에게 연결하며 이유를 설명합니다.
       예: "메뉴 관련 질문이시군요. 메뉴 전문 직원에게 연결해 드릴게요!"
    4. 고객이 불만을 표현하면 먼저 짧게 공감한 뒤 Complaints Agent로 연결하세요.
    """


def handle_handoff(
    wrapper: RunContextWrapper[RestaurantContext],
    input_data: RestaurantHandoffData,
):
    ctx = wrapper.context
    with st.sidebar:
        st.divider()
        st.markdown("**🔀 핸드오프 정보**")
        st.write(f"담당: **{input_data.to_agent_name}**")
        st.write(f"유형: {input_data.request_type}")
        st.write(f"내용: {input_data.request_description}")
        st.write(f"이유: {input_data.reason}")
        st.markdown("**📋 전달된 Context**")
        st.write(f"고객명: {ctx.customer_name}")
        st.write(f"인원: {ctx.party_size}명")
        st.write(f"식이제한: {ctx.dietary_restrictions or '없음'}")


def make_handoff(agent):
    return handoff(
        agent=agent,
        on_handoff=handle_handoff,
        input_type=RestaurantHandoffData,
        input_filter=handoff_filters.remove_all_tools,
    )


triage_agent = Agent(
    name="Triage Agent",
    instructions=dynamic_triage_agent_instructions,
    input_guardrails=[
        restaurant_guardrail,
    ],
    handoffs=[
        make_handoff(menu_agent),
        make_handoff(order_agent),
        make_handoff(reservation_agent),
        make_handoff(complaints_agent),
    ],
    output_guardrails=[restaurant_output_guardrail],
)

menu_agent.handoffs = [make_handoff(triage_agent)]
order_agent.handoffs = [make_handoff(triage_agent)]
reservation_agent.handoffs = [make_handoff(triage_agent)]
complaints_agent.handoffs = [make_handoff(triage_agent)]
