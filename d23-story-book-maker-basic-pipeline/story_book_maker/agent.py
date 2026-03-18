from google.adk.agents import SequentialAgent

from .sub_agents.illustrator import illustrator_agent
from .sub_agents.story_writer import story_writer_agent


root_agent = SequentialAgent(
    name="StoryBookMaker",
    description="5페이지 어린이 동화를 쓰고 각 페이지의 삽화를 순서대로 생성합니다.",
    sub_agents=[story_writer_agent, illustrator_agent],
)
