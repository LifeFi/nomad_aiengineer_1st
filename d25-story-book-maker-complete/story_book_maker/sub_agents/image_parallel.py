from __future__ import annotations

from typing import AsyncGenerator
from typing import ClassVar
from typing import Type

from google.adk.agents import ParallelAgent
from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.base_agent_config import BaseAgentConfig
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.context import Context
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events.event import Event
from google.genai import types
from typing_extensions import override

from ..tools import generate_page_illustration
from .illustrator import format_story_with_artifacts

PAGE_COUNT = 5


async def _page_start_callback(callback_context: CallbackContext) -> types.Content | None:
    page_number = callback_context.state.get("temp:current_illustration_page")
    if not page_number:
        return None
    callback_context.state[f"temp:image_status_{page_number}"] = (
        f"{page_number}페이지 삽화 생성 중..."
    )
    return None


def _page_progress_text(callback_context: CallbackContext, page_number: int) -> str:
    progress = sum(
        1
        for current_page in range(1, PAGE_COUNT + 1)
        if callback_context.state.get(f"illustration_page_{current_page}")
    )
    progress = min(progress, PAGE_COUNT)
    return (
        f"{page_number}페이지 삽화 생성 완료. "
        f"전체 진행률: {progress}/{PAGE_COUNT}"
    )


class PageIllustrationAgent(BaseAgent):
    config_type: ClassVar[Type[BaseAgentConfig]] = BaseAgentConfig
    page_number: int

    @override
    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        context = Context(ctx)
        context.state["temp:current_illustration_page"] = self.page_number
        yield Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            branch=ctx.branch,
            content=types.Content(
                role="model",
                parts=[
                    types.Part.from_text(
                        text=f"{self.page_number}페이지 삽화 생성 중..."
                    )
                ],
            ),
            actions=context.actions,
        )
        result = await generate_page_illustration(
            tool_context=context, page_number=self.page_number
        )

        if result.get("status") != "ok":
            yield Event(
                invocation_id=ctx.invocation_id,
                author=self.name,
                branch=ctx.branch,
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part.from_text(
                            text=(
                                f"페이지 {self.page_number} 삽화 생성 실패: "
                                f"{result.get('message', 'unknown error')}"
                            )
                        )
                    ],
                ),
                actions=context.actions,
            )
        else:
            callback_context = CallbackContext(ctx, event_actions=context.actions)
            yield Event(
                invocation_id=ctx.invocation_id,
                author=self.name,
                branch=ctx.branch,
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part.from_text(
                            text=_page_progress_text(
                                callback_context, self.page_number
                            )
                        )
                    ],
                ),
                actions=context.actions,
            )

        if ctx.is_resumable:
            ctx.set_agent_state(self.name, end_of_agent=True)
            yield self._create_agent_state_event(ctx)


def _make_page_agent(page_number: int) -> PageIllustrationAgent:
    return PageIllustrationAgent(
        name=f"Page{page_number}Illustrator",
        description=f"{page_number}페이지 삽화를 생성하는 Parallel 서브 에이전트입니다.",
        page_number=page_number,
        before_agent_callback=_page_start_callback,
    )


parallel_illustrator_agent = ParallelAgent(
    name="ParallelIllustrator",
    description="5페이지를 동시에 생성하고 완료된 페이지를 보고합니다.",
    sub_agents=[_make_page_agent(page_num) for page_num in range(1, PAGE_COUNT + 1)],
    after_agent_callback=format_story_with_artifacts,
)
