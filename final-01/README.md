# Commit Diff Quiz Graph

최근 Git 커밋의 변경 내용을 읽고, 그 맥락을 바탕으로 학습용 퀴즈를 만드는 `LangGraph` + `Textual` 프로젝트입니다.

## What It Does

- GitPython으로 커밋 메타데이터, 변경 파일 요약, patch diff를 읽습니다.
- 변경된 파일의 해당 커밋 시점 전체 코드도 함께 읽어 퀴즈 컨텍스트로 사용합니다.
- 바이너리 patch와 코드 hunk가 없는 변경은 자동으로 걸러냅니다.
- `auto`, `latest`, `selected` 모드로 퀴즈 대상 커밋을 고를 수 있습니다.
- 여러 커밋을 함께 선택해 하나의 흐름으로 묶어 퀴즈를 생성할 수 있습니다.
- 로컬 `.git` 저장소뿐 아니라 GitHub 원격 저장소 URL도 지원합니다.
- Textual TUI에서 커밋 탐색, 자동 새로고침, 멀티 선택, 결과 저장/불러오기를 지원합니다.

## Run

LangGraph 개발 서버:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run langgraph dev
```

그래프 이름은 `commit_diff_quiz` 입니다.

Textual TUI:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run commit-quiz-tui
```

또는

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run python tui.py
```

## TUI Overview

- 상단 `Repository` 바에서 `Local .git` 또는 `GitHub Repo`를 선택할 수 있습니다.
- `Local .git`에서는 실제로 탐색된 Git 루트 경로를 표시합니다.
- `GitHub Repo`에서는 `https://github.com/owner/repo` 또는 `.git` 포함 URL을 입력할 수 있습니다.
- `Open` 또는 `Reload`로 현재 저장소의 커밋 목록을 다시 불러옵니다.

## TUI Features

- 시작 시 최근 `10`개 커밋을 보여줍니다.
- 커밋 목록 마지막의 `Load More Commits (+10)`에서 `Space`를 누르면 10개씩 더 가져옵니다.
- `Load All Commits`로 현재 저장소의 전체 커밋을 한 번에 불러올 수 있습니다.
- 커밋 목록 상단에 현재 `Loaded N/Total` 상태를 표시합니다.
- 자동 새로고침으로 새 커밋이 감지되면 목록에 반영됩니다.
- 자동 새로고침으로 들어온 새 커밋은 다른 색으로 강조되고, 커서를 한 번 올리면 일반 색으로 돌아옵니다.
- 왼쪽 커밋 리스트에서 항목을 이동하면 `Commit Detail` 패널에 SHA, 작성자, 변경 파일 요약, diff preview가 표시됩니다.
- `Quiz Output`은 `md | plain` 전환을 지원합니다.
- 결과는 `Save`로 파일 저장, `Load`로 저장된 퀴즈 파일 목록에서 다시 불러올 수 있습니다.
- 저장소 로딩 중에는 `Recent Commits` 영역에 `커밋 불러오는 중.` 애니메이션이 표시됩니다.
- 퀴즈 생성 중에는 `Quiz Output` 영역에 `퀴즈 굽는중.` 애니메이션이 표시됩니다.

## TUI Controls

- `Tab` / `Shift+Tab`: 섹션 간 포커스 이동
- `Space`: 커밋 선택/해제, `Load More`, `Load All`, `Open`, `Gen`, `Save`, `Load`, `md`, `plain`
- `g`: 퀴즈 생성
- `r`: 커밋 목록 새로고침
- `q`: 종료
- `Ctrl+C`: 즉시 종료되지 않고, 짧은 시간 안에 한 번 더 눌러야 종료

## Graph Input Example

```json
{
  "messages": [
    {
      "role": "user",
      "content": "최근 커밋 기반으로 퀴즈 만들어줘. 난이도는 중간으로 해줘."
    }
  ],
  "repo_source": "local",
  "commit_mode": "auto",
  "difficulty": "medium",
  "quiz_style": "mixed"
}
```

특정 GitHub 저장소를 대상으로 할 때는 `repo_source`와 `github_repo_url`을 함께 넘길 수 있습니다.

```json
{
  "messages": [
    {
      "role": "user",
      "content": "이 저장소의 최근 변경으로 퀴즈 만들어줘"
    }
  ],
  "repo_source": "github",
  "github_repo_url": "https://github.com/nomadcoders/ai-agents-masterclass",
  "commit_mode": "auto",
  "difficulty": "medium",
  "quiz_style": "mixed"
}
```

## Notes

- 실제 퀴즈 생성에는 `OPENAI_API_KEY`가 필요합니다.
- `.env` 파일이 있으면 `load_dotenv()`로 자동 로드합니다.
- `commit_mode`는 `auto`, `latest`, `selected`를 지원합니다.
- `selected` 모드에서는 `requested_commit_sha` 또는 `requested_commit_shas`를 함께 넘기면 특정 커밋 하나 또는 여러 개 기준으로 퀴즈를 만들 수 있습니다.
- 원격 GitHub 저장소는 `.repo_cache/github` 아래에 캐시됩니다.
