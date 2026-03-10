import streamlit as st
from agents import (
    Agent,
    RunContextWrapper,
    input_guardrail,
    Runner,
    GuardrailFunctionOutput,
    handoff,
)
from agents.extensions.handoff_prompt import RECOMMENDED_PROMPT_PREFIX
from agents.extensions import handoff_filters
from restaurant_models import (
    RestaurantContext,
    RestaurantInputGuardrailOutput,
    RestaurantHandoffData,
)
from restaurant_agents.menu_agent import menu_agent
from restaurant_agents.order_agent import order_agent
from restaurant_agents.reservation_agent import reservation_agent


input_guardrail_agent = Agent(
    name="Restaurant Guardrail Agent",
    instructions="""
    고객의 요청이 레스토랑 관련 주제인지 확인하세요.
    허용 주제: 메뉴, 음식, 재료, 알레르기, 주문, 테이블 예약, 영업시간, 가격
    비허용 주제: 레스토랑과 무관한 모든 내용 (뉴스, 정치, 기술 지원 등)
    간단한 인사말(안녕하세요, 감사합니다 등)은 허용합니다.
    is_off_topic이 True이면 reason에 이유를 한국어로 작성하세요.
""",
    output_type=RestaurantInputGuardrailOutput,
)


@input_guardrail
async def restaurant_guardrail(
    wrapper: RunContextWrapper[RestaurantContext],
    agent: Agent[RestaurantContext],
    input: str,
):
    result = await Runner.run(
        input_guardrail_agent,
        input,
        context=wrapper.context,
    )

    return GuardrailFunctionOutput(
        output_info=result.final_output,
        tripwire_triggered=result.final_output.is_off_topic,
    )


def dynamic_triage_agent_instructions(
    wrapper: RunContextWrapper[RestaurantContext],
    agent: Agent[RestaurantContext],
):
    table = (
        f"{wrapper.context.table_number}번 테이블"
        if wrapper.context.table_number
        else "테이블 미지정"
    )
    restrictions = wrapper.context.dietary_restrictions or "없음"

    return f"""
    {RECOMMENDED_PROMPT_PREFIX}

    당신은 레스토랑의 안내 직원입니다. 항상 한국어로 친절하게 응대하세요.
    고객 {wrapper.context.customer_name}님을 환영합니다!

    고객 정보:
    - 위치: {table}
    - 인원: {wrapper.context.party_size}명
    - 식이 제한: {restrictions}

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

    처리 절차:
    1. 고객의 요청을 주의 깊게 듣습니다.
    2. 요청이 불명확하면 1~2가지 질문으로 명확히 합니다.
    3. 적절한 담당자에게 연결하며 이유를 설명합니다.
       예: "메뉴 관련 질문이시군요. 메뉴 전문 직원에게 연결해 드릴게요!"
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
        st.write(f"테이블: {ctx.table_number}번")
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
    ],
)
