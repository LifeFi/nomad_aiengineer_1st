# Commit Diff Quiz Graph

최근 Git 커밋의 diff를 읽고, 그 변경 내용을 학습용 퀴즈로 바꾸는 `LangGraph` 예제입니다.

## What It Does

- GitPython으로 커밋 메타데이터, 변경 파일 요약, patch diff를 읽습니다.
- 바이너리 patch와 코드 hunk가 없는 변경은 자동으로 제외합니다.
- `auto`, `latest`, `selected` 모드로 퀴즈 대상 커밋을 고를 수 있습니다.
- 여러 커밋을 함께 선택해 하나의 흐름으로 묶어 퀴즈를 생성할 수 있습니다.
- 텍스트 diff가 있으면 LLM이 한국어 퀴즈를 생성합니다.
- 텍스트 diff가 없으면 그 이유를 설명하는 fallback 응답을 돌려줍니다.
- Textual TUI에서 최근 커밋 탐색, 자동 새로고침, 멀티 선택, 추가 로딩을 지원합니다.

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

## TUI Features

- 시작 시 최근 `10`개 커밋을 보여줍니다.
- 커밋 목록 마지막 항목의 `Load More Commits (+10)`에서 `Space`를 누르면 10개씩 더 가져옵니다.
- 커밋 목록 상단에 현재 `Loaded N/Total` 상태를 표시합니다.
- 자동 새로고침으로 새 커밋이 감지되면 목록에 반영됩니다.
- 자동 새로고침으로 들어온 새 커밋은 다른 색으로 강조되고, 커서를 한 번 올리면 일반 색으로 돌아옵니다.
- 왼쪽 커밋 리스트에서 항목을 이동하면 `Commit Detail` 패널에 상세 정보와 diff preview가 표시됩니다.
- `Quiz Output`과 `Commit Detail`은 포커스 시 스크롤할 수 있습니다.

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
- Textual TUI 단축키: `space` 선택/해제 또는 load more, `g` 생성, `r` 커밋 새로고침, `q` 종료
- `Ctrl+C`는 즉시 종료되지 않고, 짧은 시간 안에 한 번 더 눌러야 종료됩니다.
