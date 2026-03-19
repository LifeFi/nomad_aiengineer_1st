import time
import signal

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import Key
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import (
    Button,
    Footer,
    Header,
    Label,
    ListItem,
    ListView,
    RadioButton,
    RadioSet,
    Static,
    TextArea,
)

from main import (
    DEFAULT_COMMIT_LIST_LIMIT,
    build_commit_context,
    count_total_commits,
    get_commit_by_sha,
    graph,
    has_more_commits,
    list_recent_commits,
)


DEFAULT_REQUEST = "최근 커밋 기반으로 퀴즈 만들어줘"
QUIT_CONFIRM_SECONDS = 1.5
AUTO_REFRESH_SECONDS = 3.0


class QuizGenerated(Message):
    def __init__(self, content: str) -> None:
        self.content = content
        super().__init__()


class QuizFailed(Message):
    def __init__(self, error_message: str) -> None:
        self.error_message = error_message
        super().__init__()


class CommitQuizApp(App):
    TITLE = "Commit Diff Quiz"
    SUB_TITLE = "Textual + LangGraph"

    CSS = """
    Screen {
        layout: vertical;
    }

    #body {
        height: 1fr;
    }

    #commit-panel {
        width: 38;
        min-width: 32;
        border: round $accent;
        padding: 0 1;
    }

    #commit-detail-panel,
    #control-panel,
    #result-panel {
        border: round $accent;
        padding: 0 1;
    }

    #commit-detail-panel {
        height: 11;
    }

    #control-panel {
        height: 11;
    }

    #result-panel {
        height: 1fr;
    }

    #commit-list {
        height: 1fr;
        margin-top: 1;
    }

    .section-title {
        text-style: bold;
        color: $accent;
        margin: 0 0 0 0;
    }

    .help-text {
        color: $text-muted;
        margin-bottom: 1;
    }

    .mode-group, .option-group {
        margin: 0 0 1 0;
    }

    .row {
        height: auto;
    }

    #request-input {
        height: 5;
        margin-bottom: 1;
    }

    #commit-detail-view {
        height: 1fr;
        margin-bottom: 0;
    }

    #generate {
        width: 100%;
        margin-top: 1;
    }

    #status {
        margin-top: 1;
        color: $text-muted;
    }

    #result-view {
        height: 1fr;
        padding-bottom: 1;
    }

    #commit-panel:focus-within,
    #commit-detail-panel:focus-within,
    #control-panel:focus-within,
    #result-panel:focus-within {
        border: round $success;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("ctrl+c", "confirm_quit", "Confirm Quit"),
        ("g", "generate_quiz", "Generate"),
        ("r", "reload_commits", "Reload Commits"),
        ("space", "toggle_commit_selection", "Toggle Commit"),
    ]

    selected_commit_index = reactive(0)

    def __init__(self) -> None:
        super().__init__()
        self.commit_list_limit = DEFAULT_COMMIT_LIST_LIMIT
        self.commits = list_recent_commits(limit=self.commit_list_limit)
        self.has_more_commits = has_more_commits(self.commit_list_limit)
        self.total_commit_count = count_total_commits()
        self.selected_commit_indices: set[int] = set()
        self.unseen_auto_refresh_commit_shas: set[str] = set()
        self.commit_detail_cache: dict[str, str] = {}
        self.last_quit_attempt_at = 0.0
        self._previous_sigint_handler = None
        self._pending_sigint = False
        self._last_seen_head_sha = self.commits[0]["sha"] if self.commits else ""
        self._last_seen_total_commit_count = self.total_commit_count

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            with Vertical(id="commit-panel"):
                yield Label("Recent Commits", classes="section-title")
                yield Static(
                    self._commit_panel_help_text(),
                    classes="help-text",
                    id="commit-panel-help",
                )
                yield ListView(*self._build_commit_items(), id="commit-list")
            with Vertical():
                with Vertical(id="commit-detail-panel"):
                    yield Label("Commit Detail", classes="section-title")
                    yield TextArea(
                        "",
                        id="commit-detail-view",
                        read_only=True,
                    )
                with Vertical(id="control-panel"):
                    yield Label("Quiz Options", classes="section-title")
                    with Horizontal(classes="row"):
                        with Vertical(classes="option-group"):
                            yield Label("Commit Mode", classes="help-text")
                            with RadioSet(id="commit-mode", classes="mode-group"):
                                yield RadioButton(
                                    "Auto Fallback", id="mode-auto", value=True
                                )
                                yield RadioButton("Latest Only", id="mode-latest")
                                yield RadioButton("Selected Commit", id="mode-selected")
                        with Vertical(classes="option-group"):
                            yield Label("Difficulty", classes="help-text")
                            with RadioSet(id="difficulty"):
                                yield RadioButton("Easy", id="difficulty-easy")
                                yield RadioButton(
                                    "Medium", id="difficulty-medium", value=True
                                )
                                yield RadioButton("Hard", id="difficulty-hard")
                        with Vertical(classes="option-group"):
                            yield Label("Style", classes="help-text")
                            with RadioSet(id="quiz-style"):
                                yield RadioButton("Mixed", id="style-mixed", value=True)
                                yield RadioButton(
                                    "Multiple Choice", id="style-multiple_choice"
                                )
                                yield RadioButton(
                                    "Short Answer", id="style-short_answer"
                                )
                                yield RadioButton("Conceptual", id="style-conceptual")
                    yield Label("Additional Request", classes="help-text")
                    yield TextArea(DEFAULT_REQUEST, id="request-input")
                    yield Button("Generate Quiz", id="generate", variant="primary")
                    yield Static("준비됨", id="status")
                with Vertical(id="result-panel"):
                    yield Label("Quiz Output", classes="section-title")
                    yield TextArea(
                        "왼쪽에서 커밋을 선택하고 Generate Quiz를 누르면 결과가 여기에 표시됩니다.",
                        id="result-view",
                        read_only=True,
                    )
        yield Footer()

    def on_mount(self) -> None:
        self._previous_sigint_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, self._handle_sigint)
        self.set_interval(0.1, self._poll_sigint)
        self.set_interval(AUTO_REFRESH_SECONDS, self._poll_commit_updates)
        commit_list = self.query_one("#commit-list", ListView)
        if self.commits:
            commit_list.index = 0
            self._show_commit_summary(0)
            self._update_commit_detail(0)
            commit_list.focus()

    def on_unmount(self) -> None:
        if self._previous_sigint_handler is not None:
            signal.signal(signal.SIGINT, self._previous_sigint_handler)

    def _build_commit_items(self) -> list[ListItem]:
        items: list[ListItem] = []
        for index, commit in enumerate(self.commits):
            items.append(ListItem(Label(self._commit_label_text(index))))
        if self.has_more_commits:
            items.append(ListItem(Label(self._load_more_label_text())))
        return items

    def _refresh_commit_list_labels(self) -> None:
        commit_list = self.query_one("#commit-list", ListView)
        for index, item in enumerate(commit_list.children):
            label_widget = item.query_one(Label)
            if index < len(self.commits):
                label_widget.update(self._commit_label_text(index))
            else:
                label_widget.update(self._load_more_label_text())

    def _commit_label_text(self, index: int) -> Text:
        commit = self.commits[index]
        prefix = Text("     ")
        if index in self.selected_commit_indices:
            prefix = Text("  ✓  ", style="bold green")
        line = Text()
        line.append_text(prefix)
        style = (
            "bold bright_cyan"
            if commit["sha"] in self.unseen_auto_refresh_commit_shas
            else ""
        )
        line.append(f"{commit['short_sha']}  {commit['subject']}", style=style)
        return line

    def _load_more_label_text(self) -> Text:
        line = Text("  +  ", style="bold cyan")
        line.append(f"Load More Commits (+{DEFAULT_COMMIT_LIST_LIMIT})", style="bold")
        return line

    def _commit_panel_help_text(self) -> str:
        return (
            f"Space로 여러 커밋 선택/해제 | "
            f"Loaded {len(self.commits)}/{self.total_commit_count}"
        )

    def _show_commit_summary(self, index: int) -> None:
        if not self.commits:
            return
        commit = self.commits[index]
        selected_count = len(self.selected_commit_indices)
        status = self.query_one("#status", Static)
        status.update(
            "\n".join(
                [
                    f"선택된 커밋: {commit['short_sha']}",
                    f"제목: {commit['subject']}",
                    f"작성자: {commit['author']}",
                    f"날짜: {commit['date']}",
                    f"멀티 선택 개수: {selected_count}",
                ]
            )
        )

    def _commit_detail_text(self, index: int) -> str:
        commit = self.commits[index]
        cached = self.commit_detail_cache.get(commit["sha"])
        if cached is not None:
            return cached

        context = build_commit_context(
            get_commit_by_sha(commit["sha"]), "selected_commit"
        )
        detail = "\n".join(
            [
                f"SHA: {context['commit_sha']}",
                f"Subject: {context['commit_subject']}",
                f"Author: {context['commit_author']}",
                f"Date: {context['commit_date']}",
                "",
                "[Changed Files]",
                context["changed_files_summary"] or "No changed files.",
                "",
                "[Diff Preview]",
                context["diff_text"]
                or "텍스트 diff가 없습니다. 이 커밋은 바이너리 파일 변경이거나 코드 hunk가 없는 변경일 수 있습니다.",
            ]
        )
        self.commit_detail_cache[commit["sha"]] = detail
        return detail

    def _update_commit_detail(self, index: int) -> None:
        detail_view = self.query_one("#commit-detail-view", TextArea)
        detail_view.text = self._commit_detail_text(index)
        detail_view.scroll_home(animate=False)

    def _current_commit_mode(self) -> str:
        pressed = self.query_one("#commit-mode", RadioSet).pressed_button
        if pressed is None:
            return "auto"
        return pressed.id.removeprefix("mode-")

    def _current_difficulty(self) -> str:
        pressed = self.query_one("#difficulty", RadioSet).pressed_button
        if pressed is None:
            return "medium"
        return pressed.id.removeprefix("difficulty-").lower()

    def _current_quiz_style(self) -> str:
        pressed = self.query_one("#quiz-style", RadioSet).pressed_button
        if pressed is None:
            return "mixed"
        return pressed.id.removeprefix("style-")

    def _current_request(self) -> str:
        text = self.query_one("#request-input", TextArea).text.strip()
        return text or DEFAULT_REQUEST

    def _selected_commit_sha(self) -> str | None:
        if not self.commits:
            return None
        return self.commits[self.selected_commit_index]["sha"]

    def _selected_commit_shas(self) -> list[str]:
        return [
            self.commits[index]["sha"] for index in sorted(self.selected_commit_indices)
        ]

    def _set_result(self, content: str) -> None:
        result_view = self.query_one("#result-view", TextArea)
        result_view.text = content
        result_view.scroll_home(animate=False)

    def _set_status(self, content: str) -> None:
        self.query_one("#status", Static).update(content)

    def _update_commit_panel_help(self) -> None:
        self.query_one("#commit-panel-help", Static).update(
            self._commit_panel_help_text()
        )

    def _refresh_commit_list_view(self) -> None:
        commit_list = self.query_one("#commit-list", ListView)
        commit_list.clear()
        for item in self._build_commit_items():
            commit_list.append(item)

    def _restore_selection_after_refresh(self) -> None:
        commit_list = self.query_one("#commit-list", ListView)
        if not self.commits:
            return

        restored_index = min(self.selected_commit_index, len(self.commits) - 1)
        commit_list.index = restored_index
        self.selected_commit_index = restored_index
        self._show_commit_summary(restored_index)
        self._update_commit_detail(restored_index)

    def _reload_commit_data(
        self,
        announce: str | None = None,
        mark_new_commits: bool = False,
    ) -> None:
        previous_commit_shas = {commit["sha"] for commit in self.commits}
        previous_selected_shas = {
            self.commits[index]["sha"]
            for index in self.selected_commit_indices
            if index < len(self.commits)
        }
        previously_highlighted_sha = None
        if self.commits and self.selected_commit_index < len(self.commits):
            previously_highlighted_sha = self.commits[self.selected_commit_index]["sha"]

        self.commits = list_recent_commits(limit=self.commit_list_limit)
        self.has_more_commits = has_more_commits(self.commit_list_limit)
        self.total_commit_count = count_total_commits()
        self._last_seen_head_sha = self.commits[0]["sha"] if self.commits else ""
        self._last_seen_total_commit_count = self.total_commit_count

        if mark_new_commits:
            for commit in self.commits:
                if commit["sha"] not in previous_commit_shas:
                    self.unseen_auto_refresh_commit_shas.add(commit["sha"])

        self.selected_commit_indices = {
            index
            for index, commit in enumerate(self.commits)
            if commit["sha"] in previous_selected_shas
        }

        if previously_highlighted_sha:
            for index, commit in enumerate(self.commits):
                if commit["sha"] == previously_highlighted_sha:
                    self.selected_commit_index = index
                    break
            else:
                self.selected_commit_index = 0
        else:
            self.selected_commit_index = 0

        self._refresh_commit_list_view()
        self._update_commit_panel_help()
        self._restore_selection_after_refresh()

        if announce:
            self._set_status(announce)

    def _focus_chain(self) -> list[Widget]:
        return [
            self.query_one("#commit-list", ListView),
            self.query_one("#commit-detail-view", TextArea),
            self.query_one("#commit-mode", RadioSet),
            self.query_one("#difficulty", RadioSet),
            self.query_one("#quiz-style", RadioSet),
            self.query_one("#result-view", TextArea),
        ]

    def _focus_index_for_widget(self, widget: Widget | None) -> int:
        if widget is None:
            return 0

        chain = self._focus_chain()
        for index, target in enumerate(chain):
            if widget is target:
                return index
            if target in list(widget.ancestors):
                return index
            if widget in list(target.ancestors):
                return index
        return 0

    def action_focus_next_section(self) -> None:
        chain = self._focus_chain()
        current_index = self._focus_index_for_widget(self.focused)
        next_index = (current_index + 1) % len(chain)
        chain[next_index].focus()

    def action_focus_previous_section(self) -> None:
        chain = self._focus_chain()
        current_index = self._focus_index_for_widget(self.focused)
        next_index = (current_index - 1) % len(chain)
        chain[next_index].focus()

    def action_confirm_quit(self) -> None:
        now = time.monotonic()
        if now - self.last_quit_attempt_at <= QUIT_CONFIRM_SECONDS:
            self.exit()
            return

        self.last_quit_attempt_at = now
        message = (
            f"종료하려면 {QUIT_CONFIRM_SECONDS:.1f}초 안에 Ctrl+C를 한 번 더 누르세요."
        )
        self._set_status(message)
        self.notify(
            message,
            title="Quit Confirmation",
            severity="warning",
            timeout=QUIT_CONFIRM_SECONDS,
        )

    def action_help_quit(self) -> None:
        self.action_confirm_quit()

    def _handle_sigint(self, signum, frame) -> None:
        self._pending_sigint = True

    def _poll_sigint(self) -> None:
        if not self._pending_sigint:
            return
        self._pending_sigint = False
        self.action_confirm_quit()

    def _poll_commit_updates(self) -> None:
        latest = list_recent_commits(limit=1)
        latest_head_sha = latest[0]["sha"] if latest else ""
        latest_total = count_total_commits()

        if (
            latest_head_sha != self._last_seen_head_sha
            or latest_total != self._last_seen_total_commit_count
        ):
            new_commit_count = max(0, latest_total - self._last_seen_total_commit_count)
            if new_commit_count:
                self.commit_list_limit += new_commit_count
            self._reload_commit_data(
                "새 커밋을 감지해 목록을 갱신했습니다.",
                mark_new_commits=True,
            )

    def on_key(self, event: Key) -> None:
        if event.key == "tab":
            event.stop()
            self.action_focus_next_section()
            return
        if event.key == "shift+tab":
            event.stop()
            self.action_focus_previous_section()
            return

    @on(ListView.Highlighted, "#commit-list")
    def handle_commit_highlight(self, event: ListView.Highlighted) -> None:
        if event.list_view.index is None:
            return
        if event.list_view.index >= len(self.commits):
            self._set_status("Space를 눌러 커밋을 더 불러오세요.")
            return
        highlighted_sha = self.commits[event.list_view.index]["sha"]
        if highlighted_sha in self.unseen_auto_refresh_commit_shas:
            self.unseen_auto_refresh_commit_shas.remove(highlighted_sha)
            self._refresh_commit_list_labels()
        self.selected_commit_index = event.list_view.index
        self._show_commit_summary(self.selected_commit_index)
        self._update_commit_detail(self.selected_commit_index)

    @on(Button.Pressed, "#generate")
    def handle_generate(self) -> None:
        self.action_generate_quiz()

    def action_toggle_commit_selection(self) -> None:
        if not self.commits:
            return
        index = self.query_one("#commit-list", ListView).index
        if index is None:
            return
        if index >= len(self.commits):
            self.action_load_more_commits()
            return
        if index in self.selected_commit_indices:
            self.selected_commit_indices.remove(index)
        else:
            self.selected_commit_indices.add(index)
        self._refresh_commit_list_labels()
        self._show_commit_summary(index)

    def action_reload_commits(self) -> None:
        self.selected_commit_indices.clear()
        self.commit_detail_cache.clear()
        self.selected_commit_index = 0
        self._reload_commit_data("커밋 목록을 새로고침했습니다.")
        self._set_result("커밋 목록을 새로고침했습니다.")

    def action_load_more_commits(self) -> None:
        previous_count = len(self.commits)
        self.commit_list_limit += DEFAULT_COMMIT_LIST_LIMIT
        self._reload_commit_data()

        loaded_count = len(self.commits)
        if loaded_count == previous_count:
            self._set_result("더 불러올 커밋이 없습니다.")
        else:
            self._set_result(f"커밋 목록을 {loaded_count}개까지 확장했습니다.")

    def action_generate_quiz(self) -> None:
        if not self.commits:
            self._set_result("표시할 커밋이 없습니다.")
            return

        payload = {
            "messages": [{"role": "user", "content": self._current_request()}],
            "commit_mode": self._current_commit_mode(),
            "difficulty": self._current_difficulty(),
            "quiz_style": self._current_quiz_style(),
        }

        selected_sha = self._selected_commit_sha()
        selected_shas = self._selected_commit_shas()
        if payload["commit_mode"] == "selected":
            if selected_shas:
                payload["requested_commit_shas"] = selected_shas
            elif selected_sha:
                payload["requested_commit_sha"] = selected_sha

        self.query_one("#generate", Button).disabled = True
        self._set_status("퀴즈 생성 중...")
        self._set_result("LangGraph를 호출하고 있습니다. 잠시만 기다려 주세요.")
        self.generate_quiz(payload)

    @work(thread=True)
    def generate_quiz(self, payload: dict) -> None:
        try:
            result = graph.invoke(
                payload,
                config={"configurable": {"thread_id": "textual-tui-session"}},
            )
        except Exception as exc:
            error_message = str(exc)
            if "OPENAI_API_KEY" in error_message:
                error_message = (
                    "텍스트 diff 기반 퀴즈 생성에는 OPENAI_API_KEY가 필요합니다."
                )
            self.post_message(QuizFailed(error_message))
            return

        final_message = result["messages"][-1]
        self.post_message(QuizGenerated(str(final_message.content)))

    @on(QuizGenerated)
    def handle_quiz_generated(self, message: QuizGenerated) -> None:
        self.query_one("#generate", Button).disabled = False
        self._set_status("완료")
        self._set_result(message.content)

    @on(QuizFailed)
    def handle_quiz_failed(self, message: QuizFailed) -> None:
        self.query_one("#generate", Button).disabled = False
        self._set_status("오류")
        self._set_result(message.error_message)


def run() -> None:
    CommitQuizApp().run()


if __name__ == "__main__":
    run()
