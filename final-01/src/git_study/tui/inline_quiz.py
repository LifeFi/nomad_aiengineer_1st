"""Inline Quiz Screen — 소스 코드 특정 위치에 앵커된 퀴즈."""

from typing import TypedDict

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Label, Static, TextArea

from ..graph import (
    InlineQuizGrade,
    InlineQuizQuestion,
    build_inline_quiz_questions,
    detect_code_language,
    get_file_content_at_commit_or_empty,
    grade_inline_answers,
)
from .code_browser import highlight_code_lines


class InlineQuizSavedState(TypedDict):
    questions: list[InlineQuizQuestion]
    answers: dict[str, str]
    grades: list[InlineQuizGrade]
    current_index: int
    known_files: dict[str, str]


QUESTION_TYPE_KO = {
    "intent": "의도",
    "behavior": "동작",
    "tradeoff": "트레이드오프",
    "vulnerability": "취약점/위험",
}


def find_anchor_line(file_content: str, anchor_snippet: str) -> int | None:
    """anchor_snippet이 파일 내 몇 번째 줄에서 시작하는지 반환 (1-based).

    첫 번째 비어있지 않은 라인을 기준으로 대략적으로 매칭하며,
    이후 라인도 순서대로 포함되어 있는지 확인한다.
    """
    file_lines = file_content.splitlines()
    snippet_lines = [l for l in anchor_snippet.strip().splitlines() if l.strip()]
    if not snippet_lines:
        return None

    first = snippet_lines[0].strip()
    for i, file_line in enumerate(file_lines):
        if first not in file_line.strip() and not file_line.strip().startswith(first[:30]):
            continue
        matched = True
        for j, sline in enumerate(snippet_lines[1:], 1):
            if i + j >= len(file_lines):
                matched = False
                break
            if sline.strip() not in file_lines[i + j]:
                matched = False
                break
        if matched:
            return i + 1  # 1-based
    return None


def render_annotated_code(
    file_content: str,
    language: str,
    current_anchor_line: int | None,
    all_markers: list[tuple[int, str, bool]],  # (line_no, label, is_current)
) -> Text:
    """파일 내용을 질문 마커와 함께 렌더링한다."""
    highlighted_lines = highlight_code_lines(file_content, language)
    marker_map: dict[int, tuple[str, bool]] = {
        line_no: (label, is_current)
        for line_no, label, is_current in all_markers
    }
    highlight_range: set[int] = set()
    if current_anchor_line:
        for j in range(current_anchor_line, current_anchor_line + 6):
            highlight_range.add(j)

    result = Text()
    for i, line_text in enumerate(highlighted_lines):
        line_no = i + 1

        if line_no in marker_map:
            label, is_current = marker_map[line_no]
            style = "bold bright_cyan" if is_current else "cyan"
            result.append(f"  ┌── {label}\n", style=style)

        if line_no in highlight_range:
            result.append(f"{line_no:4} ", style="dim")
            result.append("▌ ", style="green")
            tinted = line_text.copy()
            tinted.stylize("on color(22)")
            result.append_text(tinted)
        else:
            result.append(f"{line_no:4} ", style="dim")
            result.append("  ")
            result.append_text(line_text)
        result.append("\n")

    return result


class InlineQuizScreen(Screen):
    """코드 앵커 퀴즈 화면.

    commit_context, repo, target_commit_sha를 받아 질문을 생성하고
    사용자 답변을 수집한 뒤 채점 결과를 보여준다.
    """

    BINDINGS = [
        ("escape", "close", "Close"),
        ("left,h", "prev_question", "Prev"),
        ("right,l", "next_question", "Next"),
    ]

    CSS = """
    InlineQuizScreen {
        layout: vertical;
    }

    #iq-header {
        height: auto;
        padding: 0 1;
        background: $panel;
        align: left middle;
    }

    #iq-title {
        color: $accent;
        text-style: bold;
        margin-right: 2;
        width: auto;
    }

    #iq-q-nav {
        width: 1fr;
        align: left middle;
        height: auto;
    }

    .iq-q-btn {
        width: auto;
        min-width: 4;
        height: 1;
        padding: 0 1;
        background: transparent;
        border: none;
        color: $text-muted;
        margin-right: 1;
    }

    .iq-q-btn.iq-active {
        color: $success;
        text-style: bold;
    }

    .iq-q-btn.iq-answered {
        color: cyan;
    }

    #iq-close {
        width: auto;
        min-width: 10;
        height: 1;
        padding: 0 1;
        background: transparent;
        border: none;
        color: $text-muted;
    }

    #iq-body {
        height: 1fr;
        padding: 0;
    }

    #iq-code-panel {
        width: 1fr;
        border: round $accent;
        padding: 0 1;
        margin-right: 1;
    }

    #iq-code-file-label {
        color: $text-muted;
        height: auto;
        margin-bottom: 1;
    }

    #iq-code-scroll {
        height: 1fr;
        border: round $panel;
        background: $boost;
    }

    #iq-code-content {
        width: 1fr;
        padding: 1;
    }

    #iq-quiz-panel {
        width: 52;
        min-width: 40;
        border: round $accent;
        padding: 0 1;
    }

    #iq-answering-group {
        height: 1fr;
    }

    #iq-q-type-badge {
        height: auto;
        color: $success;
        text-style: bold;
        margin-bottom: 1;
        margin-top: 1;
    }

    #iq-question-text {
        height: auto;
        margin-bottom: 1;
        color: $text;
    }

    #iq-answer-label {
        height: auto;
        color: $text-muted;
        margin-bottom: 0;
    }

    #iq-answer-input {
        height: 1fr;
        margin-bottom: 1;
    }

    #iq-nav-row {
        height: auto;
        align: left middle;
        margin-bottom: 1;
    }

    .iq-nav-btn {
        width: auto;
        min-width: 8;
        margin-right: 1;
        background: transparent;
        border: none;
        color: cyan;
        text-style: bold;
    }

    #iq-nav-spacer {
        width: 1fr;
    }

    #iq-grade-btn {
        width: auto;
        min-width: 8;
        background: transparent;
        border: none;
        color: $success;
        text-style: bold;
    }

    #iq-results-group {
        height: 1fr;
        display: none;
    }

    #iq-results-title {
        height: auto;
        color: $accent;
        text-style: bold;
        margin: 1 0;
    }

    #iq-result-scroll {
        height: 1fr;
        border: round $panel;
    }

    #iq-result-content {
        width: 1fr;
        padding: 1;
    }

    #iq-status-bar {
        height: auto;
        padding: 0 1;
        color: $text-muted;
        background: $panel;
    }
    """

    def __init__(
        self,
        commit_context: dict,
        repo,
        target_commit_sha: str,
        saved_state: "InlineQuizSavedState | None" = None,
        cache_key: str = "",
    ) -> None:
        super().__init__()
        self.commit_context = commit_context
        self.repo = repo
        self.target_commit_sha = target_commit_sha  # 실제 git SHA
        self.cache_key = cache_key or target_commit_sha  # 앱 캐시 키
        self._saved_state = saved_state
        self.questions: list[InlineQuizQuestion] = []
        self.answers: dict[str, str] = {}
        self.grades: list[InlineQuizGrade] = []
        self.current_index = 0
        # 실제 경로 → 파일 내용 (질문 생성 전에 미리 로드)
        self._known_files: dict[str, str] = {}
        # LLM 경로 → 실제 경로 (매핑 캐시)
        self._resolved_paths: dict[str, str] = {}
        self._anchor_cache: dict[str, int | None] = {}
        self._state = "loading"
        self._anim_frame = 0

    def compose(self) -> ComposeResult:
        with Horizontal(id="iq-header"):
            yield Label("Inline Quiz", id="iq-title")
            yield Horizontal(id="iq-q-nav")
            yield Button("Esc 닫기", id="iq-close")
        with Horizontal(id="iq-body"):
            with Vertical(id="iq-code-panel"):
                yield Label("파일 로딩 중...", id="iq-code-file-label")
                with VerticalScroll(id="iq-code-scroll"):
                    yield Static("", id="iq-code-content")
            with Vertical(id="iq-quiz-panel"):
                with Vertical(id="iq-answering-group"):
                    yield Label("", id="iq-q-type-badge")
                    yield Static("질문 생성 중...", id="iq-question-text")
                    yield Label("답변:", id="iq-answer-label")
                    yield TextArea("", id="iq-answer-input")
                    with Horizontal(id="iq-nav-row"):
                        yield Button("◀ 이전", id="iq-prev", classes="iq-nav-btn")
                        yield Static("", id="iq-nav-spacer")
                        yield Button("다음 ▶", id="iq-next", classes="iq-nav-btn")
                        yield Button("채점하기", id="iq-grade-btn")
                with Vertical(id="iq-results-group"):
                    yield Label("채점 결과", id="iq-results-title")
                    with VerticalScroll(id="iq-result-scroll"):
                        yield Static("", id="iq-result-content")
        yield Static("질문을 생성하는 중입니다...", id="iq-status-bar")

    def on_mount(self) -> None:
        self.set_interval(0.35, self._animate)
        if self._saved_state is not None:
            self._restore_state(self._saved_state)
        else:
            self._generate_questions()

    def _restore_state(self, saved: "InlineQuizSavedState") -> None:
        self._known_files = saved["known_files"]
        self._on_questions_loaded(saved["questions"])
        self.answers = saved["answers"]
        self.current_index = saved["current_index"]
        if saved["grades"]:
            self.grades = saved["grades"]
            self._show_results()
        else:
            self._update_question_panel()
            self._update_code_panel()

    # ------------------------------------------------------------------
    # 애니메이션
    # ------------------------------------------------------------------

    def _animate(self) -> None:
        if self._state not in {"loading", "grading"}:
            return
        dots = "." * ((self._anim_frame % 3) + 1)
        if self._state == "loading":
            self._set_status(f"인라인 퀴즈 질문 생성 중{dots}")
            self.query_one("#iq-question-text", Static).update(f"질문 생성 중{dots}")
        elif self._state == "grading":
            self._set_status(f"채점 중{dots}")
        self._anim_frame += 1

    def _set_status(self, text: str) -> None:
        self.query_one("#iq-status-bar", Static).update(text)

    # ------------------------------------------------------------------
    # 질문 생성
    # ------------------------------------------------------------------

    def _preload_known_files(self) -> None:
        """_known_files를 채운다.

        1순위: file_context_text 파싱 (이미 내용이 있음 — git 재접근 불필요)
        2순위: git에서 전체 파일 로드 (현재 커밋 → 삭제된 파일은 부모 커밋 fallback)
        """
        import re as _re
        from ..graph import _extract_file_paths_from_summary

        file_context_text = self.commit_context.get("file_context_text", "")

        # file_context_text 에서 "FILE: path\n```lang\ncontent\n```" 블록 파싱
        for m in _re.finditer(
            r"FILE:\s+(.+?)\n```[^\n]*\n([\s\S]*?)```",
            file_context_text,
        ):
            path = m.group(1).strip()
            snippet = m.group(2)
            if path and snippet.strip():
                self._known_files[path] = snippet  # 스니펫이라도 저장

        # git에서 전체 파일 내용으로 교체 시도 (실패해도 스니펫이 남아 있음)
        all_paths: set[str] = set(self._known_files.keys())
        for p in _extract_file_paths_from_summary(
            self.commit_context.get("changed_files_summary", "")
        ):
            all_paths.add(p)

        # 부모 커밋 SHA 구하기 (삭제 커밋의 경우 부모에서 파일이 존재)
        parent_sha: str | None = None
        try:
            commit = self.repo.commit(self.target_commit_sha)
            if commit.parents:
                parent_sha = commit.parents[0].hexsha
        except Exception:
            pass

        for path in all_paths:
            full = get_file_content_at_commit_or_empty(
                self.repo, self.target_commit_sha, path
            )
            if full:
                self._known_files[path] = full  # 전체본으로 덮어씀
            elif parent_sha:
                # 현재 커밋에 없으면 (삭제된 파일) 부모 커밋에서 시도
                parent_full = get_file_content_at_commit_or_empty(
                    self.repo, parent_sha, path
                )
                if parent_full:
                    self._known_files[path] = parent_full

    @work(thread=True)
    def _generate_questions(self) -> None:
        try:
            # 1단계: 변경된 파일 내용 미리 로드
            self._preload_known_files()

            # 2단계: 질문 생성
            questions = build_inline_quiz_questions(self.commit_context)
            self.app.call_from_thread(self._on_questions_loaded, questions)
        except Exception as exc:
            self.app.call_from_thread(self._on_questions_failed, str(exc))

    def _on_questions_loaded(self, questions: list[InlineQuizQuestion]) -> None:
        self.questions = questions
        self._state = "answering"
        self._build_q_nav()
        self._update_question_panel()
        self._update_code_panel()
        loaded_paths = ", ".join(self._known_files.keys()) or "없음"
        self._set_status(
            f"질문 {len(questions)}개 생성됨  |  로드된 파일: {loaded_paths}  |  "
            "◀▶ 또는 좌우 방향키로 이동, 채점하기로 제출"
        )

    def _on_questions_failed(self, error: str) -> None:
        self._state = "answering"
        self._set_status(f"질문 생성 실패: {error[:100]}")
        self.query_one("#iq-question-text", Static).update(
            f"질문 생성에 실패했습니다.\n\n{error[:300]}"
        )

    # ------------------------------------------------------------------
    # 내비게이션 버튼 빌드
    # ------------------------------------------------------------------

    def _build_q_nav(self) -> None:
        nav = self.query_one("#iq-q-nav", Horizontal)
        nav.remove_children()
        for i, q in enumerate(self.questions):
            classes = "iq-q-btn" + (" iq-active" if i == self.current_index else "")
            btn = Button(q["id"].upper(), id=f"iq-nav-{i}", classes=classes)
            nav.mount(btn)

    def _refresh_q_nav_styles(self) -> None:
        for i, q in enumerate(self.questions):
            try:
                btn = self.query_one(f"#iq-nav-{i}", Button)
                btn.remove_class("iq-active", "iq-answered")
                if i == self.current_index:
                    btn.add_class("iq-active")
                elif self.answers.get(q["id"], "").strip():
                    btn.add_class("iq-answered")
            except Exception:
                pass

    # ------------------------------------------------------------------
    # 파일 내용 / 앵커 위치
    # ------------------------------------------------------------------

    def _resolve_to_known(self, file_path: str, anchor_snippet: str) -> str:
        """LLM이 반환한 file_path를 실제 _known_files 경로로 매핑한다.

        우선순위:
        1. 정확히 일치하는 경로
        2. suffix 일치 (LLM이 prefix를 붙인 경우)
        3. anchor_snippet이 실제로 존재하는 파일
        """
        if file_path in self._resolved_paths:
            return self._resolved_paths[file_path]

        # 1) 정확 일치
        if file_path in self._known_files:
            self._resolved_paths[file_path] = file_path
            return file_path

        # 2) suffix 일치 (known_path의 끝이 file_path와 같거나, 그 반대)
        for known_path in self._known_files:
            if known_path.endswith(file_path) or file_path.endswith(known_path):
                self._resolved_paths[file_path] = known_path
                return known_path
            # 파일명만 비교
            if known_path.split("/")[-1] == file_path.split("/")[-1]:
                self._resolved_paths[file_path] = known_path
                return known_path

        # 3) anchor_snippet으로 역탐색
        for known_path, content in self._known_files.items():
            if find_anchor_line(content, anchor_snippet):
                self._resolved_paths[file_path] = known_path
                return known_path

        # 못 찾음
        self._resolved_paths[file_path] = file_path
        return file_path

    def _get_file_content(self, q: InlineQuizQuestion) -> tuple[str, str]:
        """(실제경로, 파일내용) 반환. 못 찾으면 ("", "")."""
        file_path = q["file_path"]
        resolved = self._resolve_to_known(file_path, q["anchor_snippet"])
        content = self._known_files.get(resolved, "")
        return resolved, content

    def _get_anchor_line(self, q: InlineQuizQuestion) -> int | None:
        key = f"{q['id']}:{q['file_path']}"
        if key not in self._anchor_cache:
            _, content = self._get_file_content(q)
            self._anchor_cache[key] = find_anchor_line(content, q["anchor_snippet"])
        return self._anchor_cache[key]

    # ------------------------------------------------------------------
    # 패널 업데이트
    # ------------------------------------------------------------------

    def _update_question_panel(self) -> None:
        if not self.questions:
            return
        q = self.questions[self.current_index]
        type_ko = QUESTION_TYPE_KO.get(q["question_type"], q["question_type"])
        self.query_one("#iq-q-type-badge", Label).update(
            f"[{self.current_index + 1}/{len(self.questions)}]  {type_ko}"
        )
        self.query_one("#iq-question-text", Static).update(q["question"])
        answer_input = self.query_one("#iq-answer-input", TextArea)
        answer_input.text = self.answers.get(q["id"], "")
        answer_input.focus()
        self._refresh_q_nav_styles()

    def _update_code_panel(self) -> None:
        if not self.questions:
            return
        q = self.questions[self.current_index]
        resolved_path, content = self._get_file_content(q)
        language = detect_code_language(resolved_path) or "text"

        if not content:
            # 모든 경로 탐색에도 파일을 못 찾은 경우: anchor_snippet 직접 표시
            self.query_one("#iq-code-file-label", Label).update(
                f"[파일 없음] {q['file_path']}"
            )
            fallback = Text()
            fallback.append("앵커 코드 (파일 로드 실패)\n\n", style="dim red")
            type_ko = QUESTION_TYPE_KO.get(q["question_type"], "")
            fallback.append(
                f"  ┌── [{q['id'].upper()}] {type_ko}\n", style="bold bright_cyan"
            )
            for i, line in enumerate(q["anchor_snippet"].splitlines(), 1):
                fallback.append(f"{i:4} ", style="dim")
                fallback.append("▌ ", style="green")
                fallback.append(f"{line}\n")
            self.query_one("#iq-code-content", Static).update(fallback)
            return

        self.query_one("#iq-code-file-label", Label).update(
            f"{resolved_path}  |  {language}"
        )

        current_anchor = self._get_anchor_line(q)

        # 같은 파일에 속한 모든 질문에 마커 표시
        markers: list[tuple[int, str, bool]] = []
        for i, other_q in enumerate(self.questions):
            other_resolved, _ = self._get_file_content(other_q)
            if other_resolved != resolved_path:
                continue
            line = self._get_anchor_line(other_q)
            if line:
                type_ko = QUESTION_TYPE_KO.get(other_q["question_type"], "")
                markers.append((line, f"[{other_q['id'].upper()}] {type_ko}", i == self.current_index))

        renderable = render_annotated_code(content, language, current_anchor, markers)
        self.query_one("#iq-code-content", Static).update(renderable)

        if current_anchor and current_anchor > 5:
            self.query_one("#iq-code-scroll", VerticalScroll).scroll_to(
                y=current_anchor - 5, animate=False
            )

    # ------------------------------------------------------------------
    # 답변 저장 / 이동
    # ------------------------------------------------------------------

    def _save_current_answer(self) -> None:
        if not self.questions:
            return
        q = self.questions[self.current_index]
        self.answers[q["id"]] = self.query_one("#iq-answer-input", TextArea).text

    def _navigate_to(self, index: int) -> None:
        self._save_current_answer()
        self.current_index = max(0, min(index, len(self.questions) - 1))
        self._update_question_panel()
        self._update_code_panel()

    # ------------------------------------------------------------------
    # 이벤트 핸들러
    # ------------------------------------------------------------------

    @on(Button.Pressed, "#iq-prev")
    def handle_prev(self) -> None:
        self._navigate_to(self.current_index - 1)

    @on(Button.Pressed, "#iq-next")
    def handle_next(self) -> None:
        self._navigate_to(self.current_index + 1)

    @on(Button.Pressed, "#iq-grade-btn")
    def handle_grade(self) -> None:
        self._save_current_answer()
        self._state = "grading"
        self._anim_frame = 0
        self.query_one("#iq-grade-btn", Button).disabled = True
        self._do_grade()

    @on(Button.Pressed, "#iq-close")
    def handle_close(self) -> None:
        self.action_close()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id.startswith("iq-nav-"):
            try:
                idx = int(btn_id.removeprefix("iq-nav-"))
                self._navigate_to(idx)
                event.stop()
            except ValueError:
                pass

    def get_saved_state(self) -> "InlineQuizSavedState":
        """현재 퀴즈 상태를 직렬화 가능한 딕셔너리로 반환."""
        return InlineQuizSavedState(
            questions=self.questions,
            answers=dict(self.answers),
            grades=list(self.grades),
            current_index=self.current_index,
            known_files=dict(self._known_files),
        )

    def action_close(self) -> None:
        self._save_current_answer()
        # 앱의 캐시에 현재 상태 저장 후 닫기
        if hasattr(self.app, "save_inline_quiz_state"):
            self.app.save_inline_quiz_state(self.cache_key, self.get_saved_state())
        self.app.pop_screen()

    def action_prev_question(self) -> None:
        if self.questions:
            self._navigate_to(self.current_index - 1)

    def action_next_question(self) -> None:
        if self.questions:
            self._navigate_to(self.current_index + 1)

    # ------------------------------------------------------------------
    # 채점
    # ------------------------------------------------------------------

    @work(thread=True)
    def _do_grade(self) -> None:
        try:
            grades = grade_inline_answers(self.questions, self.answers)
            self.app.call_from_thread(self._on_grades_loaded, grades)
        except Exception as exc:
            self.app.call_from_thread(self._on_grades_failed, str(exc))

    def _on_grades_loaded(self, grades: list[InlineQuizGrade]) -> None:
        self.grades = grades
        self._state = "results"
        self._show_results()

    def _on_grades_failed(self, error: str) -> None:
        self._state = "answering"
        self.query_one("#iq-grade-btn", Button).disabled = False
        self._set_status(f"채점 실패: {error[:100]}")

    def _show_results(self) -> None:
        grade_map = {g["id"]: g for g in self.grades}
        avg_score = (
            sum(g["score"] for g in self.grades) // len(self.grades)
            if self.grades
            else 0
        )

        result = Text()
        result.append(f"평균 점수: {avg_score}점\n\n", style="bold bright_cyan")

        for q in self.questions:
            grade = grade_map.get(q["id"])
            score = grade["score"] if grade else 0
            feedback = grade["feedback"] if grade else "-"
            bar = "█" * (score // 10) + "░" * (10 - score // 10)
            type_ko = QUESTION_TYPE_KO.get(q["question_type"], q["question_type"])

            result.append(f"{q['id'].upper()}. [{type_ko}]\n", style="bold")
            result.append(f"{q['question'][:80]}\n", style="dim")
            result.append(bar, style="green")
            result.append(f"  {score}점\n", style="bold yellow")
            result.append(f"{feedback}\n\n", style="default")

        self.query_one("#iq-result-content", Static).update(result)
        self.query_one("#iq-answering-group", Vertical).display = False
        self.query_one("#iq-results-group", Vertical).display = True
        self._set_status(f"채점 완료! 평균 {avg_score}점.  Esc로 닫기.")
