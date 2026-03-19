# Commit Diff Quiz Graph

최근 Git 커밋의 diff를 읽고, 그 변경 내용을 학습용 퀴즈로 바꾸는 `LangGraph` 예제입니다.

## What It Does

- 가장 최근 커밋의 메타데이터와 변경 파일 요약을 읽습니다.
- 바이너리 patch와 코드 hunk가 없는 변경은 자동으로 제외합니다.
- 텍스트 diff가 있으면 LLM이 한국어 퀴즈를 생성합니다.
- 텍스트 diff가 없으면 그 이유를 설명하는 fallback 응답을 돌려줍니다.

## Run

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run langgraph dev
```

그래프 이름은 `commit_diff_quiz` 입니다.

터미널에서 실행하는 Textual TUI는 아래처럼 사용할 수 있습니다.

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run commit-quiz-tui
```

또는

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run python tui.py
```

## Input Example

```json
{
  "messages": [
    {
      "role": "user",
      "content": "최근 커밋 기반으로 퀴즈 만들어줘. 난이도는 중간으로 해줘."
    }
  ],
  "commit_mode": "auto",
  "difficulty": "medium",
  "quiz_style": "mixed"
}
```

## Notes

- 실제 퀴즈 생성에는 `OPENAI_API_KEY`가 필요합니다.
- `commit_mode`는 `auto`, `latest`, `selected`를 지원합니다.
- `selected` 모드에서는 `requested_commit_sha` 또는 `requested_commit_shas`를 함께 넘기면 특정 커밋 하나 또는 여러 개 기준으로 퀴즈를 만들 수 있습니다.
- Textual TUI는 기본 최근 20개 커밋을 보여주고, `Load More Commits` 버튼으로 20개씩 더 가져옵니다.
- Textual TUI 단축키: `space` 선택/해제, `g` 생성, `r` 커밋 새로고침, `q` 종료
