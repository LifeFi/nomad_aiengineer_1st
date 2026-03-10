import dotenv

dotenv.load_dotenv()

import asyncio
import streamlit as st
from agents import Runner, SQLiteSession, InputGuardrailTripwireTriggered
from restaurant_models import RestaurantContext
from restaurant_agents.triage_agent import triage_agent

st.set_page_config(
    page_title="🍽️ Restaurant Bot",
    page_icon="🍽️",
    layout="wide",
)

st.title("🍽️ 레스토랑 AI 도우미")

# session_state 기본값 초기화 (재렌더링 시 위젯 값 보존)
if "customer_name" not in st.session_state:
    st.session_state["customer_name"] = "고객"
if "table_number" not in st.session_state:
    st.session_state["table_number"] = 1
if "party_size" not in st.session_state:
    st.session_state["party_size"] = 2
if "dietary_restrictions" not in st.session_state:
    st.session_state["dietary_restrictions"] = ""

# 사이드바 - 고객 정보 입력
with st.sidebar:
    st.header("👤 고객 정보")
    st.text_input("이름", key="customer_name")
    st.number_input("테이블 번호", min_value=1, max_value=50, key="table_number")
    st.number_input("인원수", min_value=1, max_value=20, key="party_size")
    st.text_input(
        "식이 제한 (선택)",
        placeholder="예: 견과류 알레르기, 채식주의자",
        key="dietary_restrictions",
    )
    st.divider()


# RestaurantContext 생성 (session_state에서 읽어 재렌더링 후에도 값 유지)
restaurant_ctx = RestaurantContext(
    customer_name=st.session_state["customer_name"],
    table_number=st.session_state["table_number"],
    party_size=st.session_state["party_size"],
    dietary_restrictions=st.session_state["dietary_restrictions"] or None,
)

# 세션 초기화
if "restaurant_session" not in st.session_state:
    st.session_state["restaurant_session"] = SQLiteSession(
        "restaurant-chat",
        "restaurant-memory.db",
    )
session = st.session_state["restaurant_session"]

if "restaurant_agent" not in st.session_state:
    st.session_state["restaurant_agent"] = triage_agent


async def paint_history():
    messages = await session.get_items()
    for message in messages:
        if "role" in message:
            with st.chat_message(message["role"]):
                if message["role"] == "user":
                    st.write(message["content"])
                else:
                    if message["type"] == "message":
                        st.write(message["content"][0]["text"].replace("$", r"\$"))


asyncio.run(paint_history())


async def run_agent(message):
    with st.chat_message("ai"):
        text_placeholder = st.empty()
        response = ""

        st.session_state["text_placeholder"] = text_placeholder

        try:
            stream = Runner.run_streamed(
                st.session_state["restaurant_agent"],
                message,
                session=session,
                context=restaurant_ctx,
            )

            async for event in stream.stream_events():
                if event.type == "raw_response_event":
                    if event.data.type == "response.output_text.delta":
                        response += event.data.delta
                        text_placeholder.write(response.replace("$", r"\$"))

                elif event.type == "agent_updated_stream_event":
                    if (
                        st.session_state["restaurant_agent"].name
                        != event.new_agent.name
                    ):
                        st.write(
                            f"🤖 **{st.session_state['restaurant_agent'].name}** → **{event.new_agent.name}** 로 연결 중..."
                        )
                        st.session_state["restaurant_agent"] = event.new_agent
                        text_placeholder = st.empty()
                        st.session_state["text_placeholder"] = text_placeholder
                        response = ""

        except InputGuardrailTripwireTriggered:
            st.warning(
                "죄송합니다. 레스토랑 관련 질문만 도와드릴 수 있습니다. 메뉴, 주문, 예약에 대해 문의해 주세요!"
            )


message = st.chat_input("무엇을 도와드릴까요? (메뉴 안내, 주문, 예약)")

if message:
    with st.chat_message("human"):
        st.write(message)
    asyncio.run(run_agent(message))


with st.sidebar:
    st.header("⚙️ 설정")
    reset = st.button("💬 대화 초기화", use_container_width=True)
    if reset:
        asyncio.run(session.clear_session())
        st.session_state["restaurant_agent"] = triage_agent
        st.rerun()

    st.divider()
    st.caption("현재 담당 에이전트")
    st.info(f"🤖 {st.session_state['restaurant_agent'].name}")
