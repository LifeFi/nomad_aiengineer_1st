from google.adk.agents import SequentialAgent

from .sub_agents.story_writer import story_writer_agent
from .sub_agents.image_parallel import parallel_illustrator_agent


root_agent = SequentialAgent(
    name="StoryBookMaker",
    description="주제를 받아 5페이지 어린이 동화를 쓰고 ParallelAgent로 삽화를 동시에 제작합니다.",
    sub_agents=[story_writer_agent, parallel_illustrator_agent],
)
