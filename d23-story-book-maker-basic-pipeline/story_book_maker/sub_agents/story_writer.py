from google.adk.agents import Agent
from google.adk.models.lite_llm import LiteLlm

from ..models import StoryBook

MODEL = LiteLlm("openai/gpt-4o")


story_writer_agent = Agent(
    name="StoryWriterAgent",
    description="사용자가 준 주제로 5페이지 어린이 동화를 구조화 데이터로 작성합니다.",
    model=MODEL,
    include_contents="none",
    instruction="""
당신은 어린이 그림책 제작 파이프라인의 스토리 작가 에이전트입니다.

당신의 역할:
- 사용자가 요청한 주제를 읽습니다.
- 정확히 5페이지 분량의 그림책 동화를 1편 작성합니다.
- 어조는 다정하고 상상력이 풍부하며 어린아이에게 적합해야 합니다.
- 각 페이지에는 반드시 다음이 포함되어야 합니다.
  - `text`: 어린이를 위한 2~4문장의 짧은 본문
  - `visual_description`: 삽화가가 바로 그릴 수 있을 정도로 구체적인 시각 설명
- 5페이지가 하나의 자연스러운 이야기 흐름이 되도록 시작, 전개, 마무리를 갖추세요.
- 스키마에 맞는 구조화 데이터만 반환하세요.

사용자가 입력한 원래 주제는 `theme` 필드에 그대로 반영하세요.
""",
    output_schema=StoryBook,
    output_key="story_book",
)
