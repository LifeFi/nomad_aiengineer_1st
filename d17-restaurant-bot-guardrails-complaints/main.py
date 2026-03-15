import os
import uuid
from streamlit_cookies_manager import CookieManager


import asyncio
import streamlit as st
from agents import (
    InputGuardrailTripwireTriggered,
    OutputGuardrailTripwireTriggered,
    Runner,
    SQLiteSession,
)
from models import RestaurantContext
from restaurant_agents.triage_agent import triage_agent


def configure_page() -> None:
    st.set_page_config(
        page_title="Restaurant Bot",
        page_icon="🍽️",
        layout="wide",
    )


def get_chat_session_id(cookies: CookieManager) -> str:
    if not cookies.ready():
        st.stop()

    if "chat_session" not in cookies:
        cookies["chat_session"] = str(uuid.uuid4())

    return cookies["chat_session"]


def resolve_openai_api_key() -> str:
    return (
        st.session_state.get("openai_api_key", "").strip()
        or st.secrets.get("OPENAI_API_KEY")
        or os.getenv("OPENAI_API_KEY")
    )


def has_bootstrap_api_key() -> bool:
    return bool(st.secrets.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY"))


def initialize_session_state() -> None:
    defaults = {
        "openai_api_key": "",
        "customer_name": "고객",
        "party_size": 2,
        "dietary_restrictions": "",
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


configure_page()

cookies = CookieManager()
session_id = get_chat_session_id(cookies)
initialize_session_state()

st.title("🍽️ 레스토랑 AI 도우미")

with st.sidebar:
    with st.expander("🔑 API 설정", expanded=not has_bootstrap_api_key()):
        st.text_input(
            "OpenAI API Key",
            key="openai_api_key",
            placeholder="sk-...",
            help="여기에 입력한 키를 가장 먼저 사용합니다.",
        )
        sidebar_api_key = resolve_openai_api_key()
        if not sidebar_api_key:
            st.warning("OpenAI API Key를 입력하거나 환경 변수/Secrets를 설정해 주세요.")
    st.divider()

    # 사이드바 - 고객 정보 입력
    st.header("👤 고객 정보")
    st.text_input("이름", key="customer_name")
    st.number_input("인원수", min_value=1, max_value=20, key="party_size")
    st.text_input(
        "식이 제한 (선택)",
        placeholder="예: 견과류 알레르기, 채식주의자",
        key="dietary_restrictions",
    )
    st.divider()

api_key = resolve_openai_api_key()
if not api_key:
    st.stop()
os.environ["OPENAI_API_KEY"] = api_key


# RestaurantContext 생성 (session_state에서 읽어 재렌더링 후에도 값 유지)
restaurant_ctx = RestaurantContext(
    customer_name=st.session_state["customer_name"],
    party_size=st.session_state["party_size"],
    dietary_restrictions=st.session_state["dietary_restrictions"] or None,
)


def build_contextual_user_message(message: str, context: RestaurantContext) -> str:
    restrictions = context.dietary_restrictions or "없음"
    return f"""
[고객 프로필]
- 이름: {context.customer_name}
- 인원수: {context.party_size}명
- 식이 제한: {restrictions}

[사용자 메시지]
{message}
""".strip()


# 세션 초기화
if "restaurant_session" not in st.session_state:
    st.session_state["restaurant_session"] = SQLiteSession(
        session_id,
        "restaurant-memory.db",
    )
session = st.session_state["restaurant_session"]

if "restaurant_agent" not in st.session_state:
    st.session_state["restaurant_agent"] = triage_agent
if "ui_fallback_messages" not in st.session_state:
    st.session_state["ui_fallback_messages"] = []


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

    for fallback_message in st.session_state["ui_fallback_messages"]:
        with st.chat_message("ai"):
            st.write(fallback_message.replace("$", r"\$"))


asyncio.run(paint_history())


async def run_agent(message):
    with st.chat_message("ai"):
        text_placeholder = st.empty()
        status_placeholder = st.empty()
        response = ""

        st.session_state["text_placeholder"] = text_placeholder

        try:
            status_placeholder.info("응답을 검토 중입니다...")
            agent_input = build_contextual_user_message(message, restaurant_ctx)
            stream = Runner.run_streamed(
                st.session_state["restaurant_agent"],
                agent_input,
                session=session,
                context=restaurant_ctx,
            )

            async for event in stream.stream_events():
                if event.type == "raw_response_event":
                    if event.data.type == "response.output_text.delta":
                        response += event.data.delta

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
                        status_placeholder = st.empty()
                        status_placeholder.info("응답을 검토 중입니다...")
                        response = ""

            status_placeholder.empty()
            final_output = stream.final_output or response
            text_placeholder.write(str(final_output).replace("$", r"\$"))

        except InputGuardrailTripwireTriggered:
            status_placeholder.info("input guardrail 작동")
            await asyncio.sleep(1.2)
            status_placeholder.empty()
            fallback_message = "죄송합니다. 레스토랑 관련 문의만 도와드릴 수 있으며, 부적절한 표현에는 응답할 수 없습니다. 메뉴, 주문, 예약, 불만 처리와 관련해 말씀해 주세요."
            text_placeholder.write(fallback_message)
            st.session_state["ui_fallback_messages"].append(fallback_message)
        except OutputGuardrailTripwireTriggered:
            status_placeholder.empty()
            fallback_message = "죄송합니다. 해당 요청에는 안전하고 정중한 방식으로만 안내드릴 수 있습니다. 레스토랑 이용, 주문, 예약, 불편 사항 해결과 관련된 내용으로 다시 말씀해 주세요."
            text_placeholder.write(fallback_message)
            st.session_state["ui_fallback_messages"].append(fallback_message)


message = st.chat_input("무엇을 도와드릴까요? (메뉴 안내, 주문, 예약, 불만 처리)")

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
        st.session_state["ui_fallback_messages"] = []
        st.rerun()

    st.divider()
    st.caption("현재 담당 에이전트")
    st.info(f"🤖 {st.session_state['restaurant_agent'].name}")
