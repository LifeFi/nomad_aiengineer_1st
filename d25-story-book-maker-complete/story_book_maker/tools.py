import asyncio
import base64
import logging
import os
from typing import Any
from xml.sax.saxutils import escape

from google.genai import types
from google.adk.tools import ToolContext
from openai import OpenAI

from .models import IllustrationAsset, IllustrationResult, StoryBook

logger = logging.getLogger(__name__)


def _build_image_prompt(title: str, page_number: int, visual_description: str) -> str:
    return (
        f"어린이 그림책 '{title}'의 {page_number}페이지 삽화. "
        "따뜻하고 동화적인 분위기, 일관된 캐릭터 디자인, 풍부한 표정, 완성도 높은 그림책 스타일. "
        f"장면 연출: {visual_description}"
    )


def _build_svg_bytes(title: str, page_number: int, visual_description: str) -> bytes:
    description_text = escape(visual_description[:180])
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">
  <defs>
    <linearGradient id="sky" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#f7d794" />
      <stop offset="50%" stop-color="#f3a683" />
      <stop offset="100%" stop-color="#778beb" />
    </linearGradient>
    <linearGradient id="ground" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#d1ccc0" />
      <stop offset="100%" stop-color="#786fa6" />
    </linearGradient>
  </defs>
  <rect width="1024" height="1024" fill="url(#sky)" />
  <circle cx="160" cy="160" r="82" fill="#fff3b0" opacity="0.85" />
  <ellipse cx="780" cy="170" rx="130" ry="55" fill="#ffffff" opacity="0.45" />
  <ellipse cx="640" cy="210" rx="100" ry="40" fill="#ffffff" opacity="0.3" />
  <rect x="0" y="700" width="1024" height="324" fill="url(#ground)" />
  <ellipse cx="512" cy="770" rx="240" ry="95" fill="#fffaf0" opacity="0.35" />
  <circle cx="512" cy="520" r="128" fill="#ffeaa7" />
  <polygon points="430,430 470,340 525,420" fill="#f6b93b" />
  <polygon points="600,430 645,350 690,430" fill="#f6b93b" />
  <ellipse cx="512" cy="560" rx="150" ry="120" fill="#f8c291" />
  <ellipse cx="450" cy="550" rx="18" ry="24" fill="#2d3436" />
  <ellipse cx="574" cy="550" rx="18" ry="24" fill="#2d3436" />
  <polygon points="512,585 485,620 539,620" fill="#e17055" />
  <path d="M430 620 Q512 690 594 620" stroke="#6d4c41" stroke-width="12" fill="none" stroke-linecap="round" />
  <path d="M360 560 Q250 520 205 430" stroke="#f8c291" stroke-width="28" fill="none" stroke-linecap="round" />
  <path d="M665 610 Q805 640 860 560" stroke="#f8c291" stroke-width="28" fill="none" stroke-linecap="round" />
  <rect x="120" y="820" width="784" height="120" rx="28" fill="#fffaf0" opacity="0.9" />
  <text x="160" y="890" font-family="Arial, sans-serif" font-size="28" fill="#4a3f35">{description_text}</text>
</svg>"""
    return svg.encode("utf-8")


def _page_state_key(page_number: int) -> str:
    return f"illustration_page_{page_number}"


def _generate_openai_image(prompt: str) -> tuple[bytes, str]:
    timeout_seconds = float(os.getenv("STORYBOOK_IMAGE_TIMEOUT_SECONDS", "20"))
    client = OpenAI(timeout=timeout_seconds, max_retries=0)
    image = client.images.generate(
        model="gpt-image-1",
        prompt=prompt,
        size="1024x1024",
        quality="low",
        output_format="png",
    )
    image_base64 = image.data[0].b64_json
    return base64.b64decode(image_base64), "image/png"


async def _generate_openai_image_async(prompt: str) -> tuple[bytes, str]:
    # The OpenAI SDK call is sync, so run it off the event loop to preserve
    # true parallelism across page workers.
    return await asyncio.to_thread(_generate_openai_image, prompt)


def _reset_generation_state(tool_context: ToolContext, story_book: StoryBook) -> None:
    snapshot = story_book.model_dump(mode="json")
    if tool_context.state.get("_story_snapshot") != snapshot:
        tool_context.state["_story_snapshot"] = snapshot
        tool_context.state["illustrations"] = None
        for page in story_book.pages:
            tool_context.state[_page_state_key(page.page_number)] = None


def _collect_assets_from_state(
    tool_context: ToolContext, story_book: StoryBook
) -> list[IllustrationAsset]:
    assets: list[IllustrationAsset] = []
    for page in story_book.pages:
        asset_payload = tool_context.state.get(_page_state_key(page.page_number))
        if asset_payload:
            assets.append(IllustrationAsset.model_validate(asset_payload))
    return assets


async def _render_page(tool_context: ToolContext, story_book: StoryBook, page_number: int) -> dict[str, Any]:
    page = next((p for p in story_book.pages if p.page_number == page_number), None)
    if not page:
        return {
            "status": "error",
            "message": f"페이지 {page_number}을(를) 찾을 수 없습니다.",
        }

    prompt = _build_image_prompt(
        title=story_book.title,
        page_number=page.page_number,
        visual_description=page.visual_description,
    )
    filename = f"illustration_page_{page.page_number:02}.png"

    if os.getenv("OPENAI_API_KEY"):
        try:
            logger.info("Generating OpenAI image for page %s", page.page_number)
            image_bytes, mime_type = await _generate_openai_image_async(prompt)
            generator = "openai:gpt-image-1"
        except Exception as exc:
            logger.warning(
                "Falling back to SVG for page %s because OpenAI image generation failed: %s",
                page.page_number,
                exc,
            )
            image_bytes = _build_svg_bytes(
                title=story_book.title,
                page_number=page.page_number,
                visual_description=page.visual_description,
            )
            mime_type = "image/svg+xml"
            filename = f"illustration_page_{page.page_number:02}.svg"
            generator = "local:svg-fallback"
    else:
        image_bytes = _build_svg_bytes(
            title=story_book.title,
            page_number=page.page_number,
            visual_description=page.visual_description,
        )
        mime_type = "image/svg+xml"
        filename = f"illustration_page_{page.page_number:02}.svg"
        generator = "local:svg-fallback"

    artifact_version = None
    try:
        artifact_version = await tool_context.save_artifact(
            filename=filename,
            artifact=types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
        )
        logger.info(
            "Saved illustration artifact for page %s as %s (version=%s)",
            page.page_number,
            filename,
            artifact_version,
        )
    except Exception as exc:
        logger.warning(
            "Failed to save artifact for page %s (%s): %s",
            page.page_number,
            filename,
            exc,
        )

    asset = IllustrationAsset(
        page_number=page.page_number,
        prompt=prompt,
        artifact_filename=filename,
        artifact_version=artifact_version,
        mime_type=mime_type,
        image_base64=base64.b64encode(image_bytes).decode("ascii"),
        generator=generator,
    )
    logger.info(
        "Prepared illustration payload for page %s as %s via %s",
        page.page_number,
        filename,
        generator,
    )
    tool_context.state[_page_state_key(page.page_number)] = asset.model_dump(mode="json")

    return {
        "status": "ok",
        "page_number": page.page_number,
        "artifact_filename": filename,
        "generator": generator,
    }


async def generate_story_illustrations(tool_context: ToolContext) -> dict[str, Any]:
    story_payload = tool_context.state.get("story_book")
    if not story_payload:
        return {
            "status": "error",
            "message": "State에서 story_book 데이터를 찾지 못했습니다.",
        }

    story_book = StoryBook.model_validate(story_payload)
    _reset_generation_state(tool_context, story_book)
    artifacts: list[str] = []
    for page in story_book.pages:
        result = await _render_page(tool_context, story_book, page.page_number)
        if result.get("status") != "ok":
            return result
        artifacts.append(result["artifact_filename"])

    assets = _collect_assets_from_state(tool_context, story_book)
    result = IllustrationResult(title=story_book.title, images=assets)
    tool_context.state["illustrations"] = result.model_dump(mode="json")
    first_asset = assets[0] if assets else None
    return {
        "status": "ok",
        "title": story_book.title,
        "image_count": len(artifacts),
        "artifacts": artifacts,
        "generator": first_asset.generator if first_asset else "unknown",
    }


async def generate_page_illustration(tool_context: ToolContext, page_number: int) -> dict[str, Any]:
    story_payload = tool_context.state.get("story_book")
    if not story_payload:
        return {
            "status": "error",
            "message": "State에서 story_book 데이터를 찾지 못했습니다.",
        }

    story_book = StoryBook.model_validate(story_payload)
    _reset_generation_state(tool_context, story_book)
    return await _render_page(tool_context, story_book, page_number)
