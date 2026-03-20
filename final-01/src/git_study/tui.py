import json
import time
import signal
from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import Key
from textual.message import Message
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Markdown,
    MarkdownViewer,
    RadioButton,
    RadioSet,
    Static,
    TextArea,
)
from textual.widgets._markdown import MarkdownFence, MarkdownTableOfContents

from .main import (
    DEFAULT_COMMIT_LIST_LIMIT,
    build_commit_context,
    get_commit_list_snapshot,
    get_latest_commit_head,
    get_repo,
    graph,
)


DEFAULT_REQUEST = "최근 커밋 기반으로 퀴즈 만들어줘"
QUIT_CONFIRM_SECONDS = 1.5
LOCAL_COMMIT_POLL_SECONDS = 3.0
REMOTE_COMMIT_POLL_SECONDS = 30.0
STATUS_ANIMATION_SECONDS = 0.35
APP_RUNTIME_DIR = Path(__file__).resolve().parents[2] / ".git-study"
APP_STATE_PATH = APP_RUNTIME_DIR / "state.json"
QUIZ_OUTPUT_DIR = APP_RUNTIME_DIR / "outputs"


class QuizGenerated(Message):
    def __init__(self, content: str, created_at: str) -> None:
        self.content = content
        self.created_at = created_at
        super().__init__()


class QuizFailed(Message):
    def __init__(self, error_message: str) -> None:
        self.error_message = error_message
        super().__init__()


class RepoCommitsLoaded(Message):
    def __init__(
        self,
        commits: list[dict[str, str]],
        has_more_commits: bool,
        total_commit_count: int,
        announce: str,
        repo_key: str,
    ) -> None:
        self.commits = commits
        self.has_more_commits = has_more_commits
        self.total_commit_count = total_commit_count
        self.announce = announce
        self.repo_key = repo_key
        super().__init__()


class RepoCommitsFailed(Message):
    def __init__(self, error_message: str) -> None:
        self.error_message = error_message
        super().__init__()


class LabeledMarkdownFence(MarkdownFence):
    DEFAULT_CSS = """
    LabeledMarkdownFence {
        padding: 0;
        margin: 1 0;
        overflow: scroll hidden;
        scrollbar-size-horizontal: 0;
        scrollbar-size-vertical: 0;
        width: 1fr;
        height: auto;
        color: rgb(210,210,210);
        background: black 10%;
        &:light {
            background: white 30%;
        }
    }

    LabeledMarkdownFence > #code-language {
        height: auto;
        padding: 0 1;
        color: $text-muted;
        background: $panel-darken-1;
        text-style: bold;
    }

    LabeledMarkdownFence > #code-content {
        padding: 1 2;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label(self.lexer or "text", id="code-language")
        yield Label(self._highlighted_code, id="code-content")

    async def _update_from_block(self, block):
        await super()._update_from_block(block)
        self.query_one("#code-language", Label).update(self.lexer or "text")


class LabeledMarkdown(Markdown):
    BLOCKS = {
        **Markdown.BLOCKS,
        "fence": LabeledMarkdownFence,
        "code_block": LabeledMarkdownFence,
    }


class LabeledMarkdownViewer(MarkdownViewer):
    def compose(self) -> ComposeResult:
        markdown = LabeledMarkdown(
            parser_factory=self._parser_factory,
            open_links=self._open_links,
        )
        markdown.can_focus = True
        yield markdown
        yield MarkdownTableOfContents(markdown)


class ResultLoadScreen(ModalScreen[Path | None]):
    CSS = """
    #load-dialog {
        width: 72;
        max-width: 90%;
        height: 22;
        max-height: 80%;
        padding: 1 2;
        border: round $accent;
        background: $surface;
        margin: 4 0;
    }

    #load-title {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }

    #load-help {
        color: $text-muted;
        margin-bottom: 1;
    }

    #load-file-list {
        height: 1fr;
    }
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(self, files: list[Path]) -> None:
        super().__init__()
        self.files = files

    def compose(self) -> ComposeResult:
        with Vertical(id="load-dialog"):
            yield Label("Load Quiz File", id="load-title")
            yield Static("Enter 또는 Space로 선택, Esc로 닫기", id="load-help")
            yield ListView(
                *[
                    ListItem(
                        Label(
                            f"{path.name}  ({time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(path.stat().st_mtime))})"
                        )
                    )
                    for path in self.files
                ],
                id="load-file-list",
            )

    def on_mount(self) -> None:
        file_list = self.query_one("#load-file-list", ListView)
        if self.files:
            file_list.index = 0
        file_list.focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _selected_file(self) -> Path | None:
        file_list = self.query_one("#load-file-list", ListView)
        index = file_list.index
        if index is None or not (0 <= index < len(self.files)):
            return None
        return self.files[index]

    def on_key(self, event: Key) -> None:
        if event.key == "space" and self.focused is self.query_one(
            "#load-file-list", ListView
        ):
            event.stop()
            self.dismiss(self._selected_file())

    @on(ListView.Selected, "#load-file-list")
    def handle_file_selected(self) -> None:
        self.dismiss(self._selected_file())


class CommitQuizApp(App):
    TITLE = "Git Study"
    SUB_TITLE = "Learn programming through Git history"

    CSS = """
    Screen {
        layout: vertical;
    }

    #repo-bar {
        height: auto;
        border: round $accent;
        padding: 0 1;
        margin: 0;
        layout: vertical;
    }

    #repo-bar:focus-within {
        border: round $success;
    }

    #repo-bar-top,
    #repo-bar-bottom {
        height: auto;
        width: 1fr;
        align: left middle;
    }

    #repo-bar-top {
        margin-bottom: 1;
    }

    #repo-bar-title {
        width: 10;
        color: $accent;
        text-style: bold;
        margin-right: 1;
    }

    #repo-source {
        width: auto;
        layout: horizontal;
        margin-right: 1;
    }

    #repo-source > RadioButton {
        width: auto;
        margin-right: 2;
    }

    #repo-location {
        width: 1fr;
        margin-right: 1;
        height: 3;
        min-height: 3;
        margin-top: 0;
        margin-bottom: 0;
        padding: 0 1 0 1;
    }

    #repo-open {
        width: auto;
        min-width: 6;
        margin-right: 1;
        height: 3;
        min-height: 3;
        padding: 0;
        background: transparent;
        border: none;
        border-bottom: solid transparent;
        color: cyan;
        text-style: bold;
        content-align: center bottom;
    }

    #repo-open:hover,
    #repo-open:focus {
        background: transparent;
        border: none;
        border-bottom: solid cyan;
        outline: none;
        color: cyan;
        text-style: bold;
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
        height: 8;
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
        margin-bottom: 0;
    }

    RadioSet {
        margin: 0;
        padding: 0;
    }

    RadioSet > RadioButton {
        padding-top: 0;
        padding-bottom: 0;
        margin-top: 0;
        margin-bottom: 0;
    }

    .mode-group, .option-group {
        margin: 0;
    }

    .row {
        height: auto;
    }

    #request-input {
        height: 5;
        margin-bottom: 0;
    }

    #commit-detail-view {
        height: 1fr;
        margin-bottom: 0;
    }

    #status {
        margin-top: 0;
        color: $text-muted;
    }

    #result-markdown,
    #result-plain {
        height: 1fr;
        margin: 0;
        padding: 0;
    }

    #result-markdown {
        background: $surface;
    }

    #result-markdown MarkdownH2 {
        text-style: bold;
    }

    #result-plain {
        background: $surface;
        border: none;
    }

    #result-toolbar {
        height: auto;
        width: auto;
    }

    #result-actions-left,
    #result-actions-right {
        width: auto;
        height: auto;
        align: left middle;
    }

    #result-header-spacer {
        width: 1fr;
    }

    #result-command-group {
        width: auto;
        height: auto;
        margin-left: 1;
        padding: 0;
    }

    #result-mode-group {
        width: auto;
        height: auto;
        padding: 0;
    }

    .result-separator {
        width: auto;
        min-width: 1;
        margin: 0;
        padding: 0;
        color: $text-muted;
    }

    .result-tool {
        width: auto;
        min-width: 4;
        height: 1;
        min-height: 1;
        padding: 0;
        background: transparent;
        border: none;
        color: $text-muted;
    }

    .result-tool:hover,
    .result-tool:focus {
        background: transparent;
        color: $text;
        text-style: bold underline;
    }

    Button.result-toggle.-active {
        color: $success;
        text-style: bold;
    }

    Button.result-toggle.-active:focus,
    Button.result-toggle.-active:hover {
        color: $success;
        text-style: bold underline;
    }

    .result-action {
        color: cyan;
        text-style: bold;
    }

    #result-header {
        height: auto;
        width: 100%;
        align: left middle;
        margin-bottom: 0;
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
        app_state = self._load_app_state()
        self.repo_source = app_state.get("repo_source", "local")
        self.github_repo_url = app_state.get("github_repo_url", "")
        self.saved_commit_mode = app_state.get("commit_mode", "auto")
        self.saved_difficulty = app_state.get("difficulty", "medium")
        self.saved_quiz_style = app_state.get("quiz_style", "mixed")
        self.saved_request = app_state.get("request_text", DEFAULT_REQUEST)
        initial_repo_source = self.repo_source
        initial_github_repo_url = self.github_repo_url or None
        if initial_repo_source == "github" and not initial_github_repo_url:
            initial_repo_source = "local"
            self.repo_source = "local"
        try:
            initial_snapshot = get_commit_list_snapshot(
                limit=self.commit_list_limit,
                repo_source=initial_repo_source,
                github_repo_url=initial_github_repo_url,
            )
        except Exception:
            initial_snapshot = get_commit_list_snapshot(
                limit=self.commit_list_limit,
                repo_source="local",
                github_repo_url=None,
            )
            self.repo_source = "local"
        self.commits = initial_snapshot["commits"]
        self.has_more_commits = initial_snapshot["has_more_commits"]
        self.total_commit_count = initial_snapshot["total_commit_count"]
        self.selected_range_start_index: int | None = None
        self.selected_range_end_index: int | None = None
        self.unseen_auto_refresh_commit_shas: set[str] = set()
        self.commit_detail_cache: dict[str, str] = {}
        self.last_quit_attempt_at = 0.0
        self._previous_sigint_handler = None
        self._pending_sigint = False
        self._last_seen_head_sha = self.commits[0]["sha"] if self.commits else ""
        self._last_seen_total_commit_count = self.total_commit_count
        self._last_seen_repo_key = "local"
        self._last_remote_refresh_check_at = 0.0
        self.result_content = (
            "왼쪽에서 커밋을 선택하고 Generate Quiz를 누르면 결과가 여기에 표시됩니다."
        )
        self.result_view_mode = "markdown"
        self.result_metadata_expanded = False
        self._status_animation_enabled = False
        self._status_animation_frame = 0
        self._status_animation_base = "퀴즈 굽는중"
        self._result_animation_enabled = False
        self._commit_list_loading_enabled = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="repo-bar"):
            with Horizontal(id="repo-bar-top"):
                yield Label("Repository", id="repo-bar-title")
                with RadioSet(id="repo-source", classes="mode-group", compact=True):
                    yield RadioButton(
                        "Local .git", id="repo-local", value=self.repo_source == "local"
                    )
                    yield RadioButton(
                        "GitHub Repo",
                        id="repo-github",
                        value=self.repo_source == "github",
                    )
            with Horizontal(id="repo-bar-bottom"):
                yield Input(
                    placeholder="https://github.com/nomadcoders/ai-agents-masterclass",
                    id="repo-location",
                    value=self.github_repo_url,
                )
                yield Button(
                    "Open", id="repo-open", classes="result-tool result-action"
                )
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
                            with RadioSet(
                                id="commit-mode", classes="mode-group", compact=True
                            ):
                                yield RadioButton(
                                    "Auto Fallback",
                                    id="mode-auto",
                                    value=self.saved_commit_mode == "auto",
                                )
                                yield RadioButton(
                                    "Latest Only",
                                    id="mode-latest",
                                    value=self.saved_commit_mode == "latest",
                                )
                                yield RadioButton(
                                    "Selected Range",
                                    id="mode-selected",
                                    value=self.saved_commit_mode == "selected",
                                )
                        with Vertical(classes="option-group"):
                            yield Label("Difficulty", classes="help-text")
                            with RadioSet(id="difficulty", compact=True):
                                yield RadioButton(
                                    "Easy",
                                    id="difficulty-easy",
                                    value=self.saved_difficulty == "easy",
                                )
                                yield RadioButton(
                                    "Medium",
                                    id="difficulty-medium",
                                    value=self.saved_difficulty == "medium",
                                )
                                yield RadioButton(
                                    "Hard",
                                    id="difficulty-hard",
                                    value=self.saved_difficulty == "hard",
                                )
                        with Vertical(classes="option-group"):
                            yield Label("Style", classes="help-text")
                            with RadioSet(id="quiz-style", compact=True):
                                yield RadioButton(
                                    "Mixed",
                                    id="style-mixed",
                                    value=self.saved_quiz_style == "mixed",
                                )
                                yield RadioButton(
                                    "Study Session", id="style-study_session"
                                    , value=self.saved_quiz_style == "study_session"
                                )
                                yield RadioButton(
                                    "Multiple Choice",
                                    id="style-multiple_choice",
                                    value=self.saved_quiz_style == "multiple_choice",
                                )
                                yield RadioButton(
                                    "Short Answer",
                                    id="style-short_answer",
                                    value=self.saved_quiz_style == "short_answer",
                                )
                                yield RadioButton(
                                    "Conceptual",
                                    id="style-conceptual",
                                    value=self.saved_quiz_style == "conceptual",
                                )
                    yield Label("Additional Request", classes="help-text")
                    yield TextArea(self.saved_request, id="request-input")
                    yield Static("준비됨", id="status")
                with Vertical(id="result-panel"):
                    with Horizontal(id="result-header"):
                        yield Label("Quiz Output", classes="section-title")
                        with Horizontal(id="result-actions-left"):
                            with Horizontal(id="result-command-group"):
                                yield Button(
                                    "Gen",
                                    id="result-generate",
                                    classes="result-tool result-action",
                                )
                                yield Button(
                                    "Save",
                                    id="result-download",
                                    classes="result-tool result-action",
                                )
                                yield Button(
                                    "Load",
                                    id="result-load",
                                    classes="result-tool result-action",
                                )
                        yield Static("", id="result-header-spacer")
                        with Horizontal(id="result-actions-right"):
                            yield Button(
                                "meta",
                                id="result-meta-toggle",
                                classes="result-tool result-action",
                            )
                            with Horizontal(id="result-mode-group"):
                                yield Button(
                                    "md",
                                    id="result-mode-markdown",
                                    classes="result-tool result-toggle",
                                )
                                yield Static("|", classes="result-separator")
                                yield Button(
                                    "plain",
                                    id="result-mode-plain",
                                    classes="result-tool result-toggle",
                                )
                    yield LabeledMarkdownViewer(
                        self.result_content,
                        id="result-markdown",
                        show_table_of_contents=False,
                    )
                    yield TextArea(
                        self.result_content,
                        id="result-plain",
                        read_only=True,
                    )
        yield Footer()

    def on_mount(self) -> None:
        self._previous_sigint_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, self._handle_sigint)
        self.set_interval(0.1, self._poll_sigint)
        self.set_interval(LOCAL_COMMIT_POLL_SECONDS, self._poll_commit_updates)
        self.set_interval(STATUS_ANIMATION_SECONDS, self._animate_status)
        self._update_repo_context()
        commit_list = self.query_one("#commit-list", ListView)
        if self.commits:
            commit_list.index = 0
            self._show_commit_summary(0)
            self._update_commit_detail(0)
            commit_list.focus()
        self._set_result_view_mode(self.result_view_mode)
        self._save_app_state()

    def on_unmount(self) -> None:
        if self._previous_sigint_handler is not None:
            signal.signal(signal.SIGINT, self._previous_sigint_handler)

    def _build_commit_items(self) -> list[ListItem]:
        items: list[ListItem] = []
        for index, commit in enumerate(self.commits):
            items.append(ListItem(Label(self._commit_label_text(index))))
        if self.has_more_commits:
            items.append(ListItem(Label(self._load_more_label_text())))
            items.append(ListItem(Label(self._load_all_label_text())))
        return items

    def _refresh_commit_list_labels(self) -> None:
        commit_list = self.query_one("#commit-list", ListView)
        for index, item in enumerate(commit_list.children):
            label_widget = item.query_one(Label)
            if index < len(self.commits):
                label_widget.update(self._commit_label_text(index))
            elif index == len(self.commits):
                label_widget.update(self._load_more_label_text())
            else:
                label_widget.update(self._load_all_label_text())

    def _commit_label_text(self, index: int) -> Text:
        commit = self.commits[index]
        prefix = Text("   ")
        if index == self.selected_range_start_index:
            prefix = Text(" S ", style="bold green")
        elif index == self.selected_range_end_index:
            prefix = Text(" E ", style="bold green")
        elif index in self._selected_commit_indices():
            prefix = Text(" · ", style="green")
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
        line = Text(" + ", style="bold cyan")
        line.append(f"Load More Commits (+{DEFAULT_COMMIT_LIST_LIMIT})", style="bold")
        return line

    def _load_all_label_text(self) -> Text:
        line = Text(" + ", style="bold cyan")
        line.append("Load All Commits", style="bold")
        return line

    def _commit_panel_help_text(self) -> str:
        selected_count = len(self._selected_commit_indices())
        if self.selected_range_start_index is None:
            selection_help = "Space로 시작 커밋 선택"
        elif self.selected_range_end_index is None:
            selection_help = "Space로 끝 커밋 선택"
        else:
            selection_help = f"범위 선택됨 ({selected_count} commits)"
        return (
            f"{selection_help} | "
            f"Loaded {len(self.commits)}/{self.total_commit_count}"
        )

    def _show_commit_summary(self, index: int) -> None:
        if not self.commits:
            return
        commit = self.commits[index]
        selected_count = len(self._selected_commit_indices())
        range_summary = self._selected_range_summary()
        status = self.query_one("#status", Static)
        status.update(
            "\n".join(
                [
                    f"선택된 커밋: {commit['short_sha']}",
                    f"제목: {commit['subject']}",
                    f"작성자: {commit['author']}",
                    f"날짜: {commit['date']}",
                    f"선택 범위 개수: {selected_count}",
                    f"범위: {range_summary}",
                ]
            )
        )

    def _commit_detail_text(self, index: int) -> str:
        commit = self.commits[index]
        cached = self.commit_detail_cache.get(commit["sha"])
        if cached is not None:
            return cached

        repo = get_repo(**self._repo_args(refresh_remote=False))
        selected_commit = repo.commit(commit["sha"])
        context = build_commit_context(
            selected_commit,
            "selected_commit",
            repo,
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

    def _current_repo_source(self) -> str:
        pressed = self.query_one("#repo-source", RadioSet).pressed_button
        if pressed is None:
            return "local"
        return "github" if pressed.id == "repo-github" else "local"

    def _current_github_repo_url(self) -> str | None:
        url = self.query_one("#repo-location", Input).value.strip()
        return url or None

    def _repo_args(self, refresh_remote: bool = True) -> dict:
        return {
            "repo_source": self._current_repo_source(),
            "github_repo_url": self._current_github_repo_url(),
            "refresh_remote": refresh_remote,
        }

    def _current_repo_key(self) -> str:
        repo_source = self._current_repo_source()
        if repo_source == "local":
            return "local"
        return f"github:{self._current_github_repo_url() or ''}"

    def _load_app_state(self) -> dict[str, str]:
        if not APP_STATE_PATH.exists():
            return {}
        try:
            payload = json.loads(APP_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(payload, dict):
            return {}
        repo_source = payload.get("repo_source")
        github_repo_url = payload.get("github_repo_url")
        commit_mode = payload.get("commit_mode")
        difficulty = payload.get("difficulty")
        quiz_style = payload.get("quiz_style")
        request_text = payload.get("request_text")
        return {
            "repo_source": repo_source if repo_source in {"local", "github"} else "local",
            "github_repo_url": github_repo_url if isinstance(github_repo_url, str) else "",
            "commit_mode": (
                commit_mode if commit_mode in {"auto", "latest", "selected"} else "auto"
            ),
            "difficulty": (
                difficulty if difficulty in {"easy", "medium", "hard"} else "medium"
            ),
            "quiz_style": (
                quiz_style
                if quiz_style
                in {
                    "mixed",
                    "study_session",
                    "multiple_choice",
                    "short_answer",
                    "conceptual",
                }
                else "mixed"
            ),
            "request_text": (
                request_text if isinstance(request_text, str) and request_text else DEFAULT_REQUEST
            ),
        }

    def _save_app_state(self) -> None:
        APP_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "repo_source": self._current_repo_source(),
            "github_repo_url": self.github_repo_url,
            "commit_mode": self._current_commit_mode(),
            "difficulty": self._current_difficulty(),
            "quiz_style": self._current_quiz_style(),
            "request_text": self.query_one("#request-input", TextArea).text
            if self.is_mounted
            else self.saved_request,
        }
        APP_STATE_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _reset_repo_tracking(self) -> None:
        self.commit_list_limit = DEFAULT_COMMIT_LIST_LIMIT
        self._last_seen_head_sha = ""
        self._last_seen_total_commit_count = 0
        self._last_seen_repo_key = self._current_repo_key()
        self._last_remote_refresh_check_at = 0.0

    def _load_selected_repo(self, announce: str) -> None:
        self._update_repo_context()
        self._reset_repo_tracking()
        self.commit_detail_cache.clear()
        self._clear_selected_range()
        self.selected_commit_index = 0
        if (
            self._current_repo_source() == "github"
            and not self._current_github_repo_url()
        ):
            self.commits = []
            self.has_more_commits = False
            self.total_commit_count = 0
            self._refresh_commit_list_view()
            self._update_commit_panel_help()
            self._set_status("GitHub 저장소 URL을 입력해 주세요.")
            self._set_result("GitHub Repo 모드에서는 저장소 URL이 필요합니다.")
            return
        self._show_commit_list_loading("커밋 불러오는 중...")
        self._set_status("커밋 목록을 불러오는 중...")
        self._commit_list_loading_enabled = True
        self._status_animation_frame = 0
        self.query_one("#repo-open", Button).disabled = True
        self.load_repo_commits(self._repo_args(), announce, self._current_repo_key())

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

    def _selected_commit_indices(self) -> set[int]:
        if self.selected_range_start_index is None:
            return set()
        if self.selected_range_end_index is None:
            return {self.selected_range_start_index}
        start = min(self.selected_range_start_index, self.selected_range_end_index)
        end = max(self.selected_range_start_index, self.selected_range_end_index)
        return set(range(start, end + 1))

    def _selected_commit_shas(self) -> list[str]:
        return [
            self.commits[index]["sha"] for index in sorted(self._selected_commit_indices())
        ]

    def _clear_selected_range(self) -> None:
        self.selected_range_start_index = None
        self.selected_range_end_index = None

    def _selected_range_summary(self) -> str:
        if self.selected_range_start_index is None:
            return "없음"
        start_commit = self.commits[self.selected_range_start_index]
        if self.selected_range_end_index is None:
            return f"시작 {start_commit['short_sha']}"
        end_commit = self.commits[self.selected_range_end_index]
        selected_count = len(self._selected_commit_indices())
        return (
            f"{start_commit['short_sha']} ~ {end_commit['short_sha']} "
            f"({selected_count} commits)"
        )

    def _current_repository_label(self) -> str:
        if self._current_repo_source() == "github":
            return self.github_repo_url or "unknown"
        local_repo = get_repo(repo_source="local", refresh_remote=False)
        return local_repo.working_tree_dir or str(Path.cwd())

    def _result_metadata_block(self, extension: str, created_at: str | None = None) -> str:
        selected_indices = sorted(self._selected_commit_indices())
        selected_commit_lines = [
            f"- {self.commits[index]['short_sha']}: {self.commits[index]['subject']}"
            for index in selected_indices
            if index < len(self.commits)
        ]
        highlighted_commit = (
            self.commits[self.selected_commit_index]
            if self.commits and self.selected_commit_index < len(self.commits)
            else None
        )
        metadata_lines = [
            f"created_at: {created_at or time.strftime('%Y-%m-%dT%H:%M:%S%z')}",
            f"repo_source: {self._current_repo_source()}",
            f"repository: {self._current_repository_label()}",
            f"commit_mode: {self._current_commit_mode()}",
            f"difficulty: {self._current_difficulty()}",
            f"quiz_style: {self._current_quiz_style()}",
            f"selected_range: {self._selected_range_summary()}",
        ]
        if highlighted_commit is not None:
            metadata_lines.extend(
                [
                    f"highlighted_commit_sha: {highlighted_commit['sha']}",
                    f"highlighted_commit_subject: {highlighted_commit['subject']}",
                ]
            )
        if selected_commit_lines:
            metadata_lines.append("selected_commits:")
            metadata_lines.extend(selected_commit_lines)

        if extension == "md":
            return "\n".join(["---", *metadata_lines, "---", ""])
        return "\n".join(["[metadata]", *metadata_lines, ""])

    def _set_result(self, content: str) -> None:
        self.result_content = content
        has_metadata = self._split_result_metadata(content) is not None
        if has_metadata:
            self.result_metadata_expanded = False
        markdown_view = self.query_one("#result-markdown", LabeledMarkdownViewer)
        markdown_view.document.update(self._markdown_content_for_view(content))
        markdown_view.scroll_home(animate=False)
        plain_view = self.query_one("#result-plain", TextArea)
        plain_view.text = content
        plain_view.scroll_home(animate=False)
        meta_button = self.query_one("#result-meta-toggle", Button)
        meta_button.display = has_metadata
        meta_button.label = "meta -" if self.result_metadata_expanded else "meta +"

    def _markdown_content_for_view(self, content: str) -> str:
        metadata_parts = self._split_result_metadata(content)
        if metadata_parts is None:
            return content

        metadata_block, body = metadata_parts
        if not metadata_block.strip():
            return body

        if not self.result_metadata_expanded:
            return "\n".join(
                [
                    "## Metadata",
                    "",
                    "_접혀 있습니다. 상단의 `meta`를 눌러 펼칠 수 있습니다._",
                    "",
                    body,
                ]
            )

        return "\n".join(
            [
                "## Metadata",
                "",
                "```yaml",
                metadata_block,
                "```",
                "",
                body,
            ]
        )

    def _split_result_metadata(self, content: str) -> tuple[str, str] | None:
        if content.startswith("---\n"):
            parts = content.split("\n---\n", 1)
            if len(parts) != 2:
                return None
            metadata_block = parts[0][4:]
            body = parts[1].lstrip("\n")
            return metadata_block, body

        if content.startswith("[metadata]\n"):
            lines = content.splitlines()
            metadata_lines: list[str] = []
            body_start = 1
            for index in range(1, len(lines)):
                line = lines[index]
                if not line.strip():
                    body_start = index + 1
                    break
                metadata_lines.append(line)
            body = "\n".join(lines[body_start:])
            return "\n".join(metadata_lines), body

        return None

    def _set_result_view_mode(self, mode: str) -> None:
        self.result_view_mode = mode
        markdown_view = self.query_one("#result-markdown", LabeledMarkdownViewer)
        plain_view = self.query_one("#result-plain", TextArea)
        markdown_button = self.query_one("#result-mode-markdown", Button)
        plain_button = self.query_one("#result-mode-plain", Button)
        meta_button = self.query_one("#result-meta-toggle", Button)

        is_markdown = mode == "markdown"
        markdown_view.display = is_markdown
        plain_view.display = not is_markdown
        markdown_button.set_class(is_markdown, "-active")
        plain_button.set_class(not is_markdown, "-active")
        meta_button.display = is_markdown and self._split_result_metadata(self.result_content) is not None

    def _download_result(self) -> None:
        extension = "md" if self.result_view_mode == "markdown" else "txt"
        QUIZ_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        filename = QUIZ_OUTPUT_DIR / f"quiz-output-{time.strftime('%Y%m%d-%H%M%S')}.{extension}"
        file_content = self._result_content_for_save(extension)
        filename.write_text(file_content, encoding="utf-8")
        self._set_status(f"결과를 저장했습니다: {filename.name}")
        self.notify(
            f"{filename.name} 파일로 저장했습니다.",
            title="Download Complete",
            timeout=2.0,
        )

    def _result_content_for_save(self, extension: str) -> str:
        metadata_parts = self._split_result_metadata(self.result_content)
        if metadata_parts is None:
            return self.result_content

        metadata_block, body = metadata_parts
        if extension == "md":
            return self.result_content
        return "\n".join(["[metadata]", metadata_block, "", body])

    def _saved_result_files(self) -> list[Path]:
        if not QUIZ_OUTPUT_DIR.exists():
            return []
        return sorted(
            QUIZ_OUTPUT_DIR.glob("quiz-output-*.*"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )

    def _load_result_from_file(self, filename: Path) -> None:
        content = filename.read_text(encoding="utf-8")
        self._set_result(content)
        self._set_result_view_mode(
            "markdown" if filename.suffix.lower() == ".md" else "plain"
        )
        self._set_status(f"결과를 불러왔습니다: {filename.name}")
        self.notify(
            f"{filename.name} 파일을 불러왔습니다.",
            title="Load Complete",
            timeout=2.0,
        )

    def _load_result(self) -> None:
        candidates = self._saved_result_files()
        if not candidates:
            self._set_status("불러올 저장 파일이 없습니다.")
            self.notify(
                "저장된 퀴즈 파일이 없습니다.",
                title="Load Failed",
                severity="warning",
                timeout=2.0,
            )
            return

        self.push_screen(ResultLoadScreen(candidates), self._handle_loaded_result)

    def _handle_loaded_result(self, selected_file: Path | None) -> None:
        if selected_file is None:
            self._set_status("불러오기를 취소했습니다.")
            return
        self._load_result_from_file(selected_file)

    def _set_status(self, content: str) -> None:
        self.query_one("#status", Static).update(content)

    def _update_repo_context(self) -> None:
        repo_source = self._current_repo_source()
        repo_location = self.query_one("#repo-location", Input)
        repo_open = self.query_one("#repo-open", Button)
        if repo_source == "local":
            local_repo = get_repo(repo_source="local", refresh_remote=False)
            local_repo_path = local_repo.working_tree_dir or str(Path.cwd())
            repo_location.value = local_repo_path
            repo_location.tooltip = local_repo_path
            repo_open.label = "Reload"
        else:
            local_repo = get_repo(repo_source="local", refresh_remote=False)
            local_repo_path = local_repo.working_tree_dir or str(Path.cwd())
            if repo_location.value == local_repo_path:
                repo_location.value = self.github_repo_url
            repo_location.tooltip = None
            repo_open.label = "Open"
        self._save_app_state()

    def _start_status_animation(self) -> None:
        self._status_animation_enabled = True
        self._result_animation_enabled = True
        self._status_animation_frame = 0
        self._animate_status()

    def _stop_status_animation(self) -> None:
        self._status_animation_enabled = False
        self._result_animation_enabled = False

    def _animate_status(self) -> None:
        if (
            not self._status_animation_enabled
            and not self._result_animation_enabled
            and not self._commit_list_loading_enabled
        ):
            return
        dots = "." * ((self._status_animation_frame % 3) + 1)
        animated_text = f"{self._status_animation_base}{dots}"
        if self._status_animation_enabled:
            self._set_status(animated_text)
        if self._result_animation_enabled:
            self._set_result(
                "\n".join(
                    [
                        f"## {animated_text}",
                        "",
                        "잠시만 기다려 주세요.",
                    ]
                )
            )
        if self._commit_list_loading_enabled:
            self._show_commit_list_loading(f"커밋 불러오는 중{dots}")
        self._status_animation_frame += 1

    def _update_commit_panel_help(self) -> None:
        self.query_one("#commit-panel-help", Static).update(
            self._commit_panel_help_text()
        )

    def _refresh_commit_list_view(self) -> None:
        commit_list = self.query_one("#commit-list", ListView)
        commit_list.clear()
        for item in self._build_commit_items():
            commit_list.append(item)

    def _show_commit_list_loading(self, message: str) -> None:
        commit_list = self.query_one("#commit-list", ListView)
        commit_list.clear()
        commit_list.append(ListItem(Label(Text(f" {message}", style="bold cyan"))))

    def _apply_commit_snapshot(
        self,
        commits: list[dict[str, str]],
        has_more_commits: bool,
        total_commit_count: int,
        announce: str | None = None,
        mark_new_commits: bool = False,
    ) -> None:
        previous_commit_shas = {commit["sha"] for commit in self.commits}
        previous_selected_shas = [
            self.commits[index]["sha"]
            for index in sorted(self._selected_commit_indices())
            if index < len(self.commits)
        ]
        previous_start_sha = (
            self.commits[self.selected_range_start_index]["sha"]
            if self.selected_range_start_index is not None
            and self.selected_range_start_index < len(self.commits)
            else None
        )
        previous_end_sha = (
            self.commits[self.selected_range_end_index]["sha"]
            if self.selected_range_end_index is not None
            and self.selected_range_end_index < len(self.commits)
            else None
        )
        previously_highlighted_sha = None
        if self.commits and self.selected_commit_index < len(self.commits):
            previously_highlighted_sha = self.commits[self.selected_commit_index]["sha"]

        self.commits = commits
        self.has_more_commits = has_more_commits
        self.total_commit_count = total_commit_count
        self._last_seen_head_sha = self.commits[0]["sha"] if self.commits else ""
        self._last_seen_total_commit_count = self.total_commit_count

        if mark_new_commits:
            for commit in self.commits:
                if commit["sha"] not in previous_commit_shas:
                    self.unseen_auto_refresh_commit_shas.add(commit["sha"])

        sha_to_index = {commit["sha"]: index for index, commit in enumerate(self.commits)}
        self.selected_range_start_index = (
            sha_to_index.get(previous_start_sha) if previous_start_sha else None
        )
        self.selected_range_end_index = (
            sha_to_index.get(previous_end_sha) if previous_end_sha else None
        )

        if (
            previous_selected_shas
            and self.selected_range_start_index is None
            and self.selected_range_end_index is None
        ):
            surviving_indices = [
                sha_to_index[sha] for sha in previous_selected_shas if sha in sha_to_index
            ]
            if surviving_indices:
                self.selected_range_start_index = min(surviving_indices)
                self.selected_range_end_index = max(surviving_indices)

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
        current_repo_key = self._current_repo_key()
        if current_repo_key != self._last_seen_repo_key:
            self.commit_list_limit = DEFAULT_COMMIT_LIST_LIMIT
            self._last_seen_head_sha = ""
            self._last_seen_total_commit_count = 0
            self._last_seen_repo_key = current_repo_key
        repo_args = self._repo_args()
        self._update_repo_context()
        previous_total_commit_count = self.total_commit_count
        try:
            snapshot = get_commit_list_snapshot(
                limit=self.commit_list_limit,
                **repo_args,
            )
            if mark_new_commits and previous_total_commit_count:
                new_commit_count = max(
                    0, snapshot["total_commit_count"] - previous_total_commit_count
                )
                if new_commit_count:
                    target_limit = self.commit_list_limit + new_commit_count
                    if target_limit != self.commit_list_limit:
                        self.commit_list_limit = target_limit
                        snapshot = get_commit_list_snapshot(
                            limit=self.commit_list_limit,
                            **repo_args,
                        )
        except Exception as exc:
            self.commits = []
            self.has_more_commits = False
            self.total_commit_count = 0
            self._last_seen_head_sha = ""
            self._last_seen_total_commit_count = 0
            self._refresh_commit_list_view()
            self._update_commit_panel_help()
            self._set_status("저장소를 불러오지 못했습니다.")
            self._set_result(str(exc))
            return
        self._apply_commit_snapshot(
            snapshot["commits"],
            snapshot["has_more_commits"],
            snapshot["total_commit_count"],
            announce=announce,
            mark_new_commits=mark_new_commits,
        )

    def _focus_chain(self) -> list[Widget]:
        return [
            self.query_one("#repo-source", RadioSet),
            self.query_one("#repo-location", Input),
            self.query_one("#repo-open", Button),
            self.query_one("#commit-list", ListView),
            self.query_one("#commit-detail-view", TextArea),
            self.query_one("#commit-mode", RadioSet),
            self.query_one("#difficulty", RadioSet),
            self.query_one("#quiz-style", RadioSet),
            self.query_one("#result-generate", Button),
            self.query_one("#result-download", Button),
            self.query_one("#result-load", Button),
            self.query_one("#result-meta-toggle", Button),
            self.query_one("#result-mode-markdown", Button),
            self.query_one("#result-mode-plain", Button),
            (
                self.query_one("#result-markdown", LabeledMarkdownViewer)
                if self.result_view_mode == "markdown"
                else self.query_one("#result-plain", TextArea)
            ),
        ]

    def _focus_index_for_widget(self, widget: Widget | None) -> int:
        if widget is None:
            return 0

        chain = self._focus_chain()
        for index, target in enumerate(chain):
            if widget is target:
                return index
            if target in widget.ancestors:
                return index
            if widget in target.ancestors:
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
        current_repo_key = self._current_repo_key()
        if current_repo_key != self._last_seen_repo_key:
            self._last_seen_repo_key = current_repo_key
            self._last_seen_head_sha = ""
            self._last_seen_total_commit_count = 0
            self._last_remote_refresh_check_at = 0.0
            return

        if self._current_repo_source() == "github":
            now = time.monotonic()
            if (
                self._last_remote_refresh_check_at
                and now - self._last_remote_refresh_check_at < REMOTE_COMMIT_POLL_SECONDS
            ):
                return
            self._last_remote_refresh_check_at = now

        repo_args = self._repo_args()
        try:
            latest = get_latest_commit_head(**repo_args)
            latest_head_sha = latest["sha"] if latest else ""
        except Exception:
            return

        if not self._last_seen_head_sha:
            self._last_seen_head_sha = latest_head_sha
            return

        if latest_head_sha != self._last_seen_head_sha:
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
        if event.key in {"pageup", "pagedown"}:
            commit_list = self.query_one("#commit-list", ListView)
            if self.focused is commit_list and len(commit_list.children) > 0:
                event.stop()
                commit_list.index = 0 if event.key == "pageup" else len(commit_list.children) - 1
            return
        if event.key == "space":
            focused = self.focused
            if focused is self.query_one("#repo-open", Button):
                event.stop()
                self._load_selected_repo("저장소를 불러왔습니다.")
                return
            if focused is self.query_one("#result-generate", Button):
                event.stop()
                self.action_generate_quiz()
                return
            if focused is self.query_one("#result-mode-markdown", Button):
                event.stop()
                self._set_result_view_mode("markdown")
                return
            if focused is self.query_one("#result-mode-plain", Button):
                event.stop()
                self._set_result_view_mode("plain")
                return
            if focused is self.query_one("#result-load", Button):
                event.stop()
                self._load_result()
                return
            if focused is self.query_one("#result-download", Button):
                event.stop()
                self._download_result()
                return
            if focused is self.query_one("#result-meta-toggle", Button):
                event.stop()
                self._toggle_result_metadata()
                return

    @on(ListView.Highlighted, "#commit-list")
    def handle_commit_highlight(self, event: ListView.Highlighted) -> None:
        if event.list_view.index is None:
            return
        if event.list_view.index == len(self.commits):
            self._set_status("Space를 눌러 커밋을 더 불러오세요.")
            return
        if event.list_view.index == len(self.commits) + 1:
            self._set_status("Space를 눌러 커밋 전체를 불러오세요.")
            return
        highlighted_sha = self.commits[event.list_view.index]["sha"]
        if highlighted_sha in self.unseen_auto_refresh_commit_shas:
            self.unseen_auto_refresh_commit_shas.remove(highlighted_sha)
            self._refresh_commit_list_labels()
        self.selected_commit_index = event.list_view.index
        self._show_commit_summary(self.selected_commit_index)
        self._update_commit_detail(self.selected_commit_index)

    @on(Button.Pressed, "#result-generate")
    def handle_generate(self) -> None:
        self.action_generate_quiz()

    @on(Button.Pressed, "#result-mode-markdown")
    def handle_result_mode_markdown(self) -> None:
        self._set_result_view_mode("markdown")

    @on(Button.Pressed, "#result-mode-plain")
    def handle_result_mode_plain(self) -> None:
        self._set_result_view_mode("plain")

    @on(Button.Pressed, "#result-download")
    def handle_result_download(self) -> None:
        self._download_result()

    @on(Button.Pressed, "#result-load")
    def handle_result_load(self) -> None:
        self._load_result()

    @on(Button.Pressed, "#result-meta-toggle")
    def handle_result_meta_toggle(self) -> None:
        self._toggle_result_metadata()

    @on(Button.Pressed, "#repo-open")
    def handle_repo_open(self) -> None:
        self._load_selected_repo("저장소를 불러왔습니다.")

    @on(RadioSet.Changed, "#repo-source")
    def handle_repo_source_changed(self) -> None:
        self.repo_source = self._current_repo_source()
        self._update_repo_context()
        if (
            self._current_repo_source() == "github"
            and not self._current_github_repo_url()
        ):
            self.commits = []
            self.has_more_commits = False
            self.total_commit_count = 0
            self._refresh_commit_list_view()
            self._update_commit_panel_help()
            self._set_status("GitHub 저장소 URL을 입력해 주세요.")
            self._set_result("GitHub Repo 모드에서는 저장소 URL이 필요합니다.")
            return
        self._load_selected_repo("저장소를 불러왔습니다.")

    @on(RadioSet.Changed, "#commit-mode")
    @on(RadioSet.Changed, "#difficulty")
    @on(RadioSet.Changed, "#quiz-style")
    def handle_quiz_option_changed(self) -> None:
        self._save_app_state()

    @on(Input.Submitted, "#repo-location")
    def handle_github_repo_url_submitted(self) -> None:
        self._update_repo_context()
        if self._current_repo_source() != "github":
            return
        self.github_repo_url = self._current_github_repo_url() or ""
        self._save_app_state()
        self._load_selected_repo("GitHub 저장소를 불러왔습니다.")

    @on(Input.Changed, "#repo-location")
    def handle_github_repo_url_changed(self) -> None:
        if self._current_repo_source() == "github":
            self.github_repo_url = self._current_github_repo_url() or ""
            self._save_app_state()
        self._update_repo_context()

    @on(TextArea.Changed, "#request-input")
    def handle_request_changed(self) -> None:
        self._save_app_state()

    def action_toggle_commit_selection(self) -> None:
        if not self.commits:
            return
        index = self.query_one("#commit-list", ListView).index
        if index is None:
            return
        if index == len(self.commits):
            self.action_load_more_commits()
            return
        if index == len(self.commits) + 1:
            self.action_load_all_commits()
            return
        if self.selected_range_start_index is None:
            self.selected_range_start_index = index
            self.selected_range_end_index = None
        else:
            if index == self.selected_range_start_index:
                self._clear_selected_range()
            else:
                self.selected_range_end_index = index
        self._refresh_commit_list_labels()
        self._update_commit_panel_help()
        self._show_commit_summary(index)

    def action_reload_commits(self) -> None:
        self._clear_selected_range()
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

    def action_load_all_commits(self) -> None:
        self.commit_list_limit = max(self.total_commit_count, DEFAULT_COMMIT_LIST_LIMIT)
        self._reload_commit_data()
        self._set_result(f"커밋 전체 {len(self.commits)}개를 불러왔습니다.")

    @work(thread=True)
    def load_repo_commits(self, repo_args: dict, announce: str, repo_key: str) -> None:
        try:
            snapshot = get_commit_list_snapshot(
                limit=self.commit_list_limit,
                **repo_args,
            )
        except Exception as exc:
            self.post_message(RepoCommitsFailed(str(exc)))
            return

        self.post_message(
            RepoCommitsLoaded(
                commits=snapshot["commits"],
                has_more_commits=snapshot["has_more_commits"],
                total_commit_count=snapshot["total_commit_count"],
                announce=announce,
                repo_key=repo_key,
            )
        )

    def action_generate_quiz(self) -> None:
        if not self.commits:
            self._set_result("표시할 커밋이 없습니다.")
            return

        payload = {
            "messages": [{"role": "user", "content": self._current_request()}],
            "repo_source": self._current_repo_source(),
            "commit_mode": self._current_commit_mode(),
            "difficulty": self._current_difficulty(),
            "quiz_style": self._current_quiz_style(),
        }
        github_repo_url = self._current_github_repo_url()
        if payload["repo_source"] == "github":
            if not github_repo_url:
                self._set_status("GitHub 저장소 URL을 입력해 주세요.")
                self._set_result("GitHub Repo 모드에서는 저장소 URL이 필요합니다.")
                return
            payload["github_repo_url"] = github_repo_url

        selected_sha = self._selected_commit_sha()
        selected_shas = self._selected_commit_shas()
        if payload["commit_mode"] == "selected":
            if selected_shas:
                payload["requested_commit_shas"] = selected_shas
            elif selected_sha:
                payload["requested_commit_sha"] = selected_sha

        self.query_one("#result-generate", Button).disabled = True
        self._start_status_animation()
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
        self.post_message(
            QuizGenerated(
                str(final_message.content),
                time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            )
        )

    @on(QuizGenerated)
    def handle_quiz_generated(self, message: QuizGenerated) -> None:
        self.query_one("#result-generate", Button).disabled = False
        self._stop_status_animation()
        self._set_status("완료")
        self._set_result(
            f"{self._result_metadata_block('md', created_at=message.created_at)}\n{message.content}"
        )

    @on(QuizFailed)
    def handle_quiz_failed(self, message: QuizFailed) -> None:
        self.query_one("#result-generate", Button).disabled = False
        self._stop_status_animation()
        self._set_status("오류")
        self._set_result(message.error_message)

    def _toggle_result_metadata(self) -> None:
        if self._split_result_metadata(self.result_content) is None:
            return
        self.result_metadata_expanded = not self.result_metadata_expanded
        meta_button = self.query_one("#result-meta-toggle", Button)
        meta_button.label = "meta -" if self.result_metadata_expanded else "meta +"
        if self.result_view_mode == "markdown":
            markdown_view = self.query_one("#result-markdown", LabeledMarkdownViewer)
            markdown_view.document.update(self._markdown_content_for_view(self.result_content))
            markdown_view.scroll_home(animate=False)

    @on(RepoCommitsLoaded)
    def handle_repo_commits_loaded(self, message: RepoCommitsLoaded) -> None:
        self.query_one("#repo-open", Button).disabled = False
        self._commit_list_loading_enabled = False
        self._last_seen_repo_key = message.repo_key
        self.unseen_auto_refresh_commit_shas.clear()
        self._apply_commit_snapshot(
            message.commits,
            message.has_more_commits,
            message.total_commit_count,
            announce=message.announce,
        )

    @on(RepoCommitsFailed)
    def handle_repo_commits_failed(self, message: RepoCommitsFailed) -> None:
        self.query_one("#repo-open", Button).disabled = False
        self._commit_list_loading_enabled = False
        self.commits = []
        self.has_more_commits = False
        self.total_commit_count = 0
        self._last_seen_head_sha = ""
        self._last_seen_total_commit_count = 0
        self._refresh_commit_list_view()
        self._update_commit_panel_help()
        self._set_status("저장소를 불러오지 못했습니다.")
        self._set_result(message.error_message)


def run() -> None:
    CommitQuizApp().run()


if __name__ == "__main__":
    run()
