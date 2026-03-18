import os
from typing import Any
from xml.sax.saxutils import escape
import base64

from google.genai import types
from google.adk.tools import ToolContext
from openai import OpenAI

from .models import IllustrationAsset, IllustrationResult, StoryBook


def _build_image_prompt(title: str, page_number: int, visual_description: str) -> str:
    return (
        f"어린이 그림책 '{title}'의 {page_number}페이지 삽화. "
        "따뜻하고 동화적인 분위기, 일관된 캐릭터 디자인, 풍부한 표정, 완성도 높은 그림책 스타일. "
        f"장면 연출: {visual_description}"
    )


def _build_svg_bytes(title: str, page_number: int, visual_description: str) -> bytes:
    title_text = escape(title)
    description_text = escape(visual_description[:220])
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">
  <defs>
    <linearGradient id="sky" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#f7d794" />
      <stop offset="50%" stop-color="#f3a683" />
      <stop offset="100%" stop-color="#778beb" />
    </linearGradient>
  </defs>
  <rect width="1024" height="1024" fill="url(#sky)" />
  <circle cx="160" cy="160" r="82" fill="#fff3b0" opacity="0.85" />
  <rect x="96" y="660" width="832" height="220" rx="28" fill="#fffaf0" opacity="0.92" />
  <text x="96" y="120" font-family="Georgia, serif" font-size="42" fill="#fffaf0">{page_number}페이지</text>
  <text x="96" y="200" font-family="Georgia, serif" font-size="56" fill="#ffffff">{title_text}</text>
  <text x="96" y="740" font-family="Arial, sans-serif" font-size="30" fill="#4a3f35">{description_text}</text>
</svg>"""
    return svg.encode("utf-8")


def _generate_openai_image(prompt: str) -> tuple[bytes, str]:
    client = OpenAI()
    image = client.images.generate(
        model="gpt-image-1",
        prompt=prompt,
        size="1024x1024",
        quality="low",
        output_format="png",
    )
    image_base64 = image.data[0].b64_json
    return base64.b64decode(image_base64), "image/png"


async def generate_story_illustrations(tool_context: ToolContext) -> dict[str, Any]:
    story_payload = tool_context.state.get("story_book")
    if not story_payload:
        return {
            "status": "error",
            "message": "State에서 story_book 데이터를 찾지 못했습니다.",
        }

    story_book = StoryBook.model_validate(story_payload)
    assets: list[IllustrationAsset] = []

    for page in story_book.pages:
        prompt = _build_image_prompt(
            title=story_book.title,
            page_number=page.page_number,
            visual_description=page.visual_description,
        )
        filename = f"illustration_page_{page.page_number:02}.png"

        if os.getenv("OPENAI_API_KEY"):
            image_bytes, mime_type = _generate_openai_image(prompt)
            generator = "openai:gpt-image-1"
        else:
            image_bytes = _build_svg_bytes(
                title=story_book.title,
                page_number=page.page_number,
                visual_description=page.visual_description,
            )
            mime_type = "image/svg+xml"
            filename = f"illustration_page_{page.page_number:02}.svg"
            generator = "local:svg-fallback"

        artifact_version = await tool_context.save_artifact(
            filename=filename,
            artifact=types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
        )

        assets.append(
            IllustrationAsset(
                page_number=page.page_number,
                prompt=prompt,
                artifact_filename=filename,
                artifact_version=artifact_version,
                mime_type=mime_type,
                generator=generator,
            )
        )

    result = IllustrationResult(title=story_book.title, images=assets)
    tool_context.state["illustrations"] = result.model_dump(mode="json")

    return {
        "status": "ok",
        "title": story_book.title,
        "image_count": len(assets),
        "artifacts": [asset.artifact_filename for asset in assets],
        "generator": assets[0].generator if assets else "unknown",
    }
