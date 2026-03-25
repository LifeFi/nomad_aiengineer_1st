from pydantic import BaseModel, Field


class StoryPage(BaseModel):
    page_number: int = Field(ge=1, le=5)
    text: str = Field(
        description="어린이 그림책 한 페이지에 들어갈 짧은 본문 텍스트입니다."
    )
    visual_description: str = Field(
        description="해당 페이지 삽화를 위한 구체적인 시각 연출 설명입니다."
    )


class StoryBook(BaseModel):
    title: str
    theme: str = Field(description="사용자가 전달한 동화의 주제입니다.")
    age_range: str = Field(
        description="예: 4-7세처럼 권장 독자 연령대를 나타냅니다."
    )
    moral: str = Field(description="이 동화가 전달하는 교훈 또는 감정적 메시지입니다.")
    pages: list[StoryPage] = Field(
        min_length=5,
        max_length=5,
        description="정확히 5페이지로 구성된 동화 본문 데이터입니다.",
    )


class IllustrationAsset(BaseModel):
    page_number: int
    prompt: str
    artifact_filename: str | None = None
    artifact_version: int | None = None
    mime_type: str
    image_base64: str = Field(
        description="최종 출력에 직접 삽입할 수 있는 base64 인코딩 이미지 데이터입니다."
    )
    generator: str = Field(
        description="이미지가 OpenAI 생성인지 로컬 SVG 폴백인지 나타냅니다."
    )


class IllustrationResult(BaseModel):
    title: str
    images: list[IllustrationAsset] = Field(min_length=5, max_length=5)
