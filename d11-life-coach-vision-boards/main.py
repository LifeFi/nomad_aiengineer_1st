import dotenv

dotenv.load_dotenv()
from openai import APIError, OpenAI
import asyncio
import base64
import json
import os
import streamlit as st
from agents import (
    Agent,
    Runner,
    SQLiteSession,
    WebSearchTool,
    FileSearchTool,
    ImageGenerationTool,
    function_tool,
)
from agents.models.openai_responses import OpenAIResponsesModel

client = OpenAI()
# print(f'OPENAI_DEFAULT_MODEL: {os.getenv("OPENAI_DEFAULT_MODEL")}')

# https://platform.openai.com/storage 에서 생성 및 입력
VECTOR_STORE_ID = "vs_69995ba7d5148191a91dc5ed4b72c711"

if "session" not in st.session_state:
    st.session_state["session"] = SQLiteSession(
        "chat-history",
        "life-coach-memory.db",
    )
session = st.session_state["session"]
DEFAULT_AI_AVATAR = "assets/coach-avatar.svg"


if "ai_avatar" not in st.session_state or not os.path.exists(
    st.session_state["ai_avatar"]
):
    st.session_state["ai_avatar"] = DEFAULT_AI_AVATAR

avatar_changed = False


def patch_openai_responses_input_sanitizer():
    if getattr(OpenAIResponsesModel, "_action_sanitizer_patched", False):
        return

    original = OpenAIResponsesModel._remove_openai_responses_api_incompatible_fields

    def patched(self, list_input):
        cleaned = original(self, list_input)
        sanitized = []
        for item in cleaned:
            if isinstance(item, dict):
                normalized = item.copy()
                # image_generation_call items must not contain top-level "action"
                # when sent back as input to Responses API.
                if normalized.get("type") == "image_generation_call":
                    normalized.pop("action", None)
                sanitized.append(normalized)
            else:
                sanitized.append(item)
        return sanitized

    OpenAIResponsesModel._remove_openai_responses_api_incompatible_fields = patched
    OpenAIResponsesModel._action_sanitizer_patched = True


patch_openai_responses_input_sanitizer()


def chat_message_with_avatar(role: str):
    if role in ("assistant", "ai"):
        avatar = st.session_state.get("ai_avatar", DEFAULT_AI_AVATAR)
        if not os.path.exists(avatar):
            avatar = DEFAULT_AI_AVATAR
            st.session_state["ai_avatar"] = avatar
        return st.chat_message(role, avatar=avatar)
    return st.chat_message(role)


def find_latest_generated_image_b64(items) -> str | None:
    for item in reversed(items):
        if (
            isinstance(item, dict)
            and item.get("type") == "image_generation_call"
            and item.get("result")
        ):
            return item["result"]
    return None


@function_tool
async def change_avatar() -> str:
    """Change the assistant avatar to the most recently generated image.

    Call this only when the user asks to change/update the assistant avatar.
    """
    image_b64 = st.session_state.get("latest_generated_image_b64")
    if not image_b64:
        session_items = await session.get_items()
        image_b64 = find_latest_generated_image_b64(session_items)
    if not image_b64:
        return (
            "아바타로 바꿀 최근 생성 이미지가 없습니다. "
            "먼저 이미지를 생성한 뒤 다시 요청해주세요."
        )

    avatar_path = "assets/ai-avatar-generated.jpg"
    os.makedirs("assets", exist_ok=True)
    with open(avatar_path, "wb") as f:
        f.write(base64.b64decode(image_b64))

    global avatar_changed
    avatar_changed = True
    st.session_state["ai_avatar"] = avatar_path
    return "아바타를 방금 생성된 이미지로 변경했습니다."


async def sanitize_session_history():
    items = await session.get_items()
    sanitized_items = []
    changed = False

    for item in items:
        if isinstance(item, dict) and "action" in item:
            sanitized_item = item.copy()
            sanitized_item.pop("action", None)
            sanitized_items.append(sanitized_item)
            changed = True
        else:
            sanitized_items.append(item)

    if changed:
        await session.clear_session()
        await session.add_items(sanitized_items)


async def paint_history():
    messages = await session.get_items()

    for message in messages:
        if "role" in message:
            with chat_message_with_avatar(message["role"]):
                if message["role"] == "user":
                    content = message["content"]
                    if isinstance(content, str):
                        st.write(content)
                    elif isinstance(content, list):
                        for part in content:
                            if "image_url" in part:
                                st.image(part["image_url"])

                else:
                    if message["type"] == "message":
                        st.write(message["content"][0]["text"].replace("$", r"\$"))
        if "type" in message:
            message_type = message["type"]
            if message_type == "web_search_call":
                with chat_message_with_avatar("ai"):
                    st.write("🔍 Searched the web...")
            elif message_type == "file_search_call":
                with chat_message_with_avatar("ai"):
                    st.write("🗂️ Searched your files...")
            elif message_type == "image_generation_call":
                st.session_state["latest_generated_image_b64"] = message["result"]
                image = base64.b64decode(message["result"])
                with chat_message_with_avatar("ai"):
                    st.image(image)


asyncio.run(sanitize_session_history())
asyncio.run(paint_history())


def update_status(status_container, event_data):
    event_type = getattr(event_data, "type", None)

    status_messages = {
        "response.web_search_call.completed": (
            "✅ Web search completed.",
            "complete",
        ),
        "response.web_search_call.in_progress": (
            "🔍 Starting web search...",
            "running",
        ),
        "response.web_search_call.searching": (
            "🔍 Web search in progress...",
            "running",
        ),
        "response.file_search_call.completed": (
            "✅ File search completed.",
            "complete",
        ),
        "response.file_search_call.in_progress": (
            "🗂️ Starting file search...",
            "running",
        ),
        "response.file_search_call.searching": (
            "🗂️ File search in progress...",
            "running",
        ),
        "response.image_generation_call.generating": (
            "🎨 Drawing image...",
            "running",
        ),
        "response.image_generation_call.in_progress": (
            "🎨 Drawing image...",
            "running",
        ),
        "response.completed": (" ", "complete"),
    }

    if event_type in status_messages:
        label, state = status_messages[event_type]
        status_container.update(label=label, state=state)
        return

    # Function tool calls (e.g. change_avatar) are emitted as output item events.
    if event_type in ("response.output_item.added", "response.output_item.done"):
        item = getattr(event_data, "item", None)
        item_type = getattr(item, "type", None)
        item_name = getattr(item, "name", None)
        item_status = getattr(item, "status", None)

        if item_type == "function_call" and item_name == "change_avatar":
            if (
                event_type == "response.output_item.added"
                or item_status == "in_progress"
            ):
                status_container.update(label="🖼️ Changing avatar...", state="running")
            elif (
                item_status == "completed" or event_type == "response.output_item.done"
            ):
                status_container.update(label="✅ Avatar changed.", state="complete")
            elif item_status == "incomplete":
                status_container.update(
                    label="⚠️ Avatar change incomplete.", state="error"
                )


async def run_agent(message):

    agent = Agent(
        name="Life Coach Assistant",
        instructions="""
    You are an enthusiastic and encouraging life coach assistant.
    Always respond with warmth, positivity, and genuine support. Celebrate the user's efforts, validate their feelings, and motivate them to take action. Speak like a trusted coach who believes in the user's potential.

    You have access to the followign tools:
        - Web Search Tool: Use this when the user asks a questions that isn't in your training data. Use this tool when the users asks about current or future events, when you think you don't know the answer, try searching for it in the web first.
        - File Search Tool: Use this tool when the user asks a question about facts related to themselves. Or when they ask questions about specific files.
        - change_avatar: Use this tool when the user asks to change your avatar/profile image to the latest generated image.

    IMPORTANT: Always use the Web Search Tool before answering. Do not rely on your training data alone.

    You MUST use the Web Search Tool in the following situations:
        - Any question about motivation, mindset, or personal growth
        - Requests for self-improvement tips, productivity strategies, or goal-setting advice
        - Questions about habit formation, routines, or lifestyle changes
        - Any topic related to mental health, well-being, or stress management
        - When the user asks for book recommendations, resources, or exercises
        - When you are even slightly unsure about the latest research or best practices

    When in doubt, search first. Always prefer fresh, real-world information over assumptions.

    """,
        tools=[
            WebSearchTool(),
            FileSearchTool(
                vector_store_ids=[VECTOR_STORE_ID],
                max_num_results=3,
            ),
            ImageGenerationTool(
                tool_config={
                    "type": "image_generation",
                    "quality": "high",
                    "output_format": "jpeg",
                    "partial_images": 1,
                }
            ),
            change_avatar,
        ],
    )
    print(f"agent.model: {agent.model} / agent.model_settings: {agent.model_settings}")

    with chat_message_with_avatar("ai"):
        status_container = st.status("⏳", expanded=False)
        text_placeholder = st.empty()
        image_placeholder = st.empty()
        response = ""

        st.session_state["text_placeholder"] = text_placeholder
        st.session_state["image_placeholder"] = image_placeholder

        stream = Runner.run_streamed(
            agent,
            message,
            session=session,
        )

        try:
            async for event in stream.stream_events():
                if hasattr(event, "model_dump"):
                    print(json.dumps(event.model_dump(), ensure_ascii=False, indent=2))
                else:
                    print(json.dumps(str(event), ensure_ascii=False, indent=2))
                if event.type == "raw_response_event":

                    update_status(status_container, event.data)

                    if event.data.type == "response.output_text.delta":
                        response += event.data.delta
                        text_placeholder.write(response.replace("$", r"\$"))
                    elif (
                        event.data.type
                        == "response.image_generation_call.partial_image"
                    ):
                        st.session_state["latest_generated_image_b64"] = (
                            event.data.partial_image_b64
                        )
                        image = base64.b64decode(event.data.partial_image_b64)
                        image_placeholder.image(image)
        except APIError as e:
            message_text = str(e).lower()
            if "quota" in message_text or "billing" in message_text:
                status_container.update(
                    label="⚠️ API quota exceeded. Check OpenAI billing/credits.",
                    state="error",
                )
                text_placeholder.error(
                    "현재 OpenAI 사용 한도를 초과했습니다. "
                    "플랫폼에서 결제/크레딧 상태를 확인한 뒤 다시 시도해주세요."
                )
            else:
                status_container.update(label="⚠️ OpenAI API error.", state="error")
                text_placeholder.error(f"API 오류가 발생했습니다: {e}")
        except Exception as e:
            status_container.update(label="⚠️ Unexpected error.", state="error")
            text_placeholder.error(f"예상치 못한 오류가 발생했습니다: {e}")


prompt = st.chat_input(
    "라이프 코치님께 무엇이든 물어보세요!",
    accept_file=True,
    file_type=[
        "txt",
        "pdf",
    ],
)


if prompt:

    if "text_placeholder" in st.session_state:
        st.session_state["text_placeholder"].empty()
    if "image_placeholder" in st.session_state:
        st.session_state["image_placeholder"].empty()

    for file in prompt.files:
        if file.type.startswith("text/") or file.type == "application/pdf":
            with chat_message_with_avatar("ai"):
                with st.status(f"⏳ Uploading {file.name}...") as status:
                    uploaded_file = client.files.create(
                        file=(file.name, file.getvalue()),
                        purpose="user_data",
                    )
                    status.update(label="⏳ Attaching file...")
                    client.vector_stores.files.create(
                        vector_store_id=VECTOR_STORE_ID,
                        file_id=uploaded_file.id,
                    )
                    status.update(label=f"✅ {file.name} uploaded", state="complete")

    if prompt.text:
        with chat_message_with_avatar("human"):
            st.write(prompt.text)
        asyncio.run(run_agent(prompt.text))
    elif prompt.files:
        asyncio.run(
            run_agent(
                f"{len(prompt.files)}개 파일이 업로드되었습니다. 파일 내용에 대해 질문해주세요."
            )
        )


with st.sidebar:
    reset = st.button("Reset memory")
    if reset:
        asyncio.run(session.clear_session())
        st.rerun()
    if avatar_changed:
        st.rerun()

    st.write(asyncio.run(session.get_items()))
