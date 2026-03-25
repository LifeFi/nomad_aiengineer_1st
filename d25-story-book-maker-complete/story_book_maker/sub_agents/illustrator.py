import base64

from google.adk.agents.callback_context import CallbackContext
from google.genai import types

from ..models import IllustrationResult, StoryBook


def _collect_illustrations_from_page_state(callback_context: CallbackContext) -> IllustrationResult | None:
    story_payload = callback_context.state.get("story_book")
    if not story_payload:
        return None

    story_book = StoryBook.model_validate(story_payload)
    images = []
    for page in story_book.pages:
        asset_payload = callback_context.state.get(f"illustration_page_{page.page_number}")
        if asset_payload:
            images.append(asset_payload)

    if len(images) != len(story_book.pages):
        return None

    result = IllustrationResult.model_validate(
        {"title": story_book.title, "images": images}
    )
    callback_context.state["illustrations"] = result.model_dump(mode="json")
    return result


async def format_story_with_artifacts(
    callback_context: CallbackContext,
) -> types.Content | None:
    story_payload = callback_context.state.get("story_book")
    illustration_payload = callback_context.state.get("illustrations")

    if not story_payload:
        return None

    if not illustration_payload:
        recovered = _collect_illustrations_from_page_state(callback_context)
        if not recovered:
            return None
        illustration_payload = recovered.model_dump(mode="json")

    story_book = StoryBook.model_validate(story_payload)
    illustration_result = IllustrationResult.model_validate(illustration_payload)
    image_by_page = {image.page_number: image for image in illustration_result.images}

    parts: list[types.Part] = []
    parts.append(
        types.Part.from_text(
            text=(
                f"완성된 동화책: {story_book.title}\n"
                f"주제: {story_book.theme}\n"
                f"연령대: {story_book.age_range}\n"
                f"교훈: {story_book.moral}\n"
            )
        )
    )
    parts.append(types.Part.from_text(text=""))
    for page in story_book.pages:
        image = image_by_page.get(page.page_number)
        illustration_label = (
            f"삽화: [Artifact 저장됨: {image.artifact_filename}]"
            if image and image.artifact_version is not None
            else "삽화: [Artifact 저장 실패, inline 이미지로 표시됨]"
            if image
            else "삽화: [이미지를 찾을 수 없습니다]"
        )
        parts.append(
            types.Part.from_text(
                text="\n".join(
                    [
                        f"페이지: {page.page_number}",
                        f"내용: {page.text}",
                        illustration_label,
                        "",
                    ]
                )
            )
        )

        if image:
            artifact_loaded = None
            if image.artifact_filename and image.artifact_version is not None:
                try:
                    artifact_loaded = await callback_context.load_artifact(
                        image.artifact_filename, version=image.artifact_version
                    )
                except Exception:
                    artifact_loaded = None

            if artifact_loaded and artifact_loaded.inline_data:
                parts.append(
                    types.Part(
                        inline_data=types.Blob(
                            mime_type=artifact_loaded.inline_data.mime_type,
                            data=artifact_loaded.inline_data.data,
                        )
                    )
                )
            else:
                parts.append(
                    types.Part(
                        inline_data=types.Blob(
                            mime_type=image.mime_type,
                            data=base64.b64decode(image.image_base64),
                        )
                    )
                )
            parts.append(types.Part.from_text(text=""))

    return types.Content(role="model", parts=parts)
