from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.lite_llm import LiteLlm
from google.genai import types

from ..models import IllustrationResult, StoryBook
from ..tools import generate_story_illustrations

MODEL = LiteLlm("openai/gpt-4o")


async def format_story_with_artifacts(
    callback_context: CallbackContext,
) -> types.Content | None:
    story_payload = callback_context.state.get("story_book")
    illustration_payload = callback_context.state.get("illustrations")

    if not story_payload or not illustration_payload:
        return None

    story_book = StoryBook.model_validate(story_payload)
    illustration_result = IllustrationResult.model_validate(illustration_payload)
    image_by_page = {image.page_number: image for image in illustration_result.images}

    parts: list[types.Part] = []
    for page in story_book.pages:
        image = image_by_page.get(page.page_number)
        image_label = (
            "Image: [생성된 이미지가 Artifact로 저장됨]"
            if image
            else "Image: [이미지를 찾을 수 없습니다]"
        )
        parts.append(
            types.Part.from_text(
                text="\n".join(
                    [
                        f"Page {page.page_number}:",
                        f'Text: "{page.text}"',
                        f'Visual: "{page.visual_description}"',
                        image_label,
                        "",
                    ]
                )
            )
        )

        if image:
            artifact = await callback_context.load_artifact(image.artifact_filename)
            if artifact and artifact.inline_data:
                parts.append(
                    types.Part(
                        inline_data=types.Blob(
                            mime_type=artifact.inline_data.mime_type,
                            data=artifact.inline_data.data,
                        )
                    )
                )
                parts.append(types.Part.from_text(text=""))

    return types.Content(role="model", parts=parts)


illustrator_agent = Agent(
    name="IllustratorAgent",
    description="State에 저장된 동화를 읽고 페이지별 삽화를 생성합니다.",
    model=MODEL,
    include_contents="none",
    instruction="""
당신은 순차형 그림책 워크플로우의 삽화 에이전트입니다.

스토리 작가 에이전트가 이미 구조화된 동화 데이터를 세션 State의 `story_book`에 저장해 두었습니다.
당신은 다음을 수행해야 합니다.
- 기존 `story_book` State를 읽습니다.
- `generate_story_illustrations` 도구를 정확히 한 번 호출합니다.
- 5개 페이지 각각에 대해 이미지 아티팩트를 생성하거나 저장합니다.
- 최종 삽화 메타데이터를 State의 `illustrations`에 저장합니다.
- 도구 실행이 끝나면 사용자에게 무엇이 생성되었는지 짧게 요약합니다.

동화를 다시 쓰지 마세요. 주제를 다시 물어보지 마세요.
""",
    tools=[generate_story_illustrations],
    after_agent_callback=format_story_with_artifacts,
)
