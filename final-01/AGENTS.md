# AGENTS.md

이 문서는 이 저장소에서 작업하는 코딩 에이전트를 위한 빠른 작업 가이드입니다.

## 목적

- Git 커밋의 변경 맥락을 읽고 학습용 퀴즈를 생성하는 앱을 유지보수합니다.
- 주요 사용자 흐름은 `LangGraph` 백엔드와 `Textual` TUI 두 축으로 나뉩니다.
- 최근 추가된 인라인 퀴즈 기능은 일반 퀴즈와 별도 흐름이지만, 같은 커밋 컨텍스트를 재사용합니다.

## 빠른 실행

```bash
UV_CACHE_DIR=/tmp/uv-cache uv sync
UV_CACHE_DIR=/tmp/uv-cache uv run git-study
UV_CACHE_DIR=/tmp/uv-cache uv run langgraph dev
```

필수 환경 변수:

```bash
OPENAI_API_KEY=...
```

## 작업 전 확인할 파일

- `README.md`
  사용자 문서. 기능이 바뀌면 함께 갱신합니다.
- `src/git_study/graph.py`
  저장소 로딩, 커밋 컨텍스트 수집, 퀴즈 프롬프트, 인라인 퀴즈 생성/채점 핵심 로직이 있습니다.
- `src/git_study/tui/app.py`
  메인 앱의 레이아웃과 이벤트 흐름이 있습니다.
- `src/git_study/tui/inline_quiz.py`
  인라인 퀴즈 UI와 상태 복원 로직이 있습니다.
- `src/git_study/tui/code_browser.py`
  코드 diff 표시 관련 로직이 있습니다.

## 아키텍처 메모

### 일반 퀴즈 흐름

1. `get_repo()`가 로컬 또는 GitHub 저장소를 엽니다.
2. `collect_commit_context()`가 커밋/범위의 diff와 파일 컨텍스트를 만듭니다.
3. `build_quiz()`가 LLM 프롬프트를 구성해 markdown 결과를 생성합니다.
4. TUI가 결과를 렌더링하고 저장할 수 있게 합니다.

### 인라인 퀴즈 흐름

1. TUI에서 현재 선택 커밋의 `build_commit_context()`를 가져옵니다.
2. `InlineQuizScreen`이 변경 파일 내용을 미리 로드합니다.
3. `build_inline_quiz_questions()`가 파일 경로와 앵커 스니펫이 포함된 질문을 생성합니다.
4. 사용자가 답변하면 `grade_inline_answers()`가 점수와 피드백을 만듭니다.

## 수정 시 주의점

- `commit_mode`는 `auto`, `latest`, `selected` 세 가지만 가정합니다.
- GitHub 저장소 URL은 `normalize_github_repo_url()` 규칙을 따릅니다.
- diff가 없거나 바이너리만 바뀐 경우도 정상 흐름으로 처리해야 합니다.
- 인라인 퀴즈의 `anchor_snippet`은 실제 파일 내용과 어긋날 수 있으니, 경로 fallback과 앵커 탐색 로직을 깨지 않게 수정합니다.
- 상태 저장 경로는 `.git-study/` 아래입니다. 사용자의 로컬 상태를 불필요하게 초기화하지 않습니다.
- 문서성 파일을 추가하거나 기능을 바꾸면 `README.md`도 같이 맞춥니다.

## 검증 팁

- 문서만 바꿨다면 `git diff --stat`과 렌더링 관점 검토로 충분할 수 있습니다.
- 코드 변경 시에는 최소한 관련 진입 경로를 직접 실행해보는 편이 좋습니다.
- LLM 호출을 쓰는 기능은 네트워크/키 의존성이 있으므로, 실행 불가 시 정적 검토 범위를 명확히 남깁니다.

## 권장 작업 방식

- 작은 기능 수정이라도 `graph.py`와 TUI 연결 지점을 같이 확인합니다.
- 새 UI 액션을 넣을 때는 버튼, 키 바인딩, 상태 메시지, 저장 상태 반영 여부를 함께 점검합니다.
- 저장 포맷을 건드릴 때는 `result_metadata.py`와 `state.py`를 같이 봅니다.
