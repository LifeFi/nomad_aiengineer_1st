import hashlib
from pathlib import Path, PurePath
from typing import Annotated, Literal, NotRequired, TypedDict

from dotenv import load_dotenv
from git import NULL_TREE, Repo
from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, BaseMessage
from langgraph.graph import END, START, StateGraph, add_messages

load_dotenv()


MAX_DIFF_CHARS = 12_000
MAX_COMMITS_TO_SCAN = 8
DEFAULT_COMMIT_LIST_LIMIT = 10
MAX_FILE_CONTEXT_CHARS = 12_000
MAX_FILE_CONTEXT_FILES = 5
MAX_FILE_SNIPPET_CHARS = 3_000
REMOTE_REPO_CACHE_DIR = Path(".repo_cache/github")


class State(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    repo_source: NotRequired[Literal["local", "github"]]
    github_repo_url: NotRequired[str]
    commit_mode: NotRequired[Literal["auto", "latest", "selected"]]
    requested_commit_sha: NotRequired[str]
    requested_commit_shas: NotRequired[list[str]]
    difficulty: NotRequired[str]
    quiz_style: NotRequired[str]
    commit_sha: str
    commit_subject: str
    commit_author: str
    commit_date: str
    changed_files_summary: str
    diff_text: str
    file_context_text: str
    selected_reason: str


class CommitListSnapshot(TypedDict):
    commits: list[dict[str, str]]
    has_more_commits: bool
    total_commit_count: int


class CommitHead(TypedDict):
    sha: str
    short_sha: str
    subject: str
    author: str
    date: str


def sanitize_diff(raw_diff: str) -> str:
    if not raw_diff.strip():
        return ""

    sections = raw_diff.split("diff --git ")
    cleaned_sections: list[str] = []

    for index, section in enumerate(sections):
        if not section.strip():
            continue

        normalized = section if index == 0 else f"diff --git {section}"
        if "GIT binary patch" in normalized:
            continue
        if "Binary files " in normalized:
            continue
        if "@@" not in normalized:
            continue
        cleaned_sections.append(normalized.strip())

    cleaned = "\n\n".join(cleaned_sections)
    return cleaned[:MAX_DIFF_CHARS].strip()


def slugify_repo_url(github_repo_url: str) -> str:
    normalized = normalize_github_repo_url(github_repo_url)
    tail = normalized.split("github.com/")[-1].replace("/", "__")
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:8]
    return f"{tail}--{digest}"


def normalize_github_repo_url(github_repo_url: str) -> str:
    normalized = github_repo_url.strip().rstrip("/")
    if normalized.startswith("github.com/"):
        normalized = f"https://{normalized}"
    if not normalized.startswith(("http://", "https://")):
        raise ValueError(
            "GitHub 저장소 URL은 https://github.com/owner/repo 형식이어야 합니다."
        )
    if "github.com/" not in normalized:
        raise ValueError("현재는 GitHub 저장소 URL만 지원합니다.")
    if not normalized.endswith(".git"):
        normalized = f"{normalized}.git"
    return normalized


def get_repo(
    repo_source: Literal["local", "github"] = "local",
    github_repo_url: str | None = None,
    refresh_remote: bool = True,
) -> Repo:
    if repo_source == "local":
        return Repo(".", search_parent_directories=True)

    if not github_repo_url:
        raise ValueError("github repo source requires github_repo_url")
    github_repo_url = normalize_github_repo_url(github_repo_url)

    cache_dir = REMOTE_REPO_CACHE_DIR / slugify_repo_url(github_repo_url)
    cache_dir.parent.mkdir(parents=True, exist_ok=True)

    if cache_dir.exists():
        repo = Repo(cache_dir)
        origin = repo.remotes.origin
        origin.set_url(github_repo_url)
        if refresh_remote:
            origin.fetch(prune=True)
        return repo

    return Repo.clone_from(github_repo_url, cache_dir)


def list_recent_commits(
    limit: int = DEFAULT_COMMIT_LIST_LIMIT,
    repo_source: Literal["local", "github"] = "local",
    github_repo_url: str | None = None,
    refresh_remote: bool = True,
) -> list[dict[str, str]]:
    return get_commit_list_snapshot(
        limit=limit,
        repo_source=repo_source,
        github_repo_url=github_repo_url,
        refresh_remote=refresh_remote,
    )["commits"]


def get_latest_commit_head(
    repo_source: Literal["local", "github"] = "local",
    github_repo_url: str | None = None,
    refresh_remote: bool = True,
) -> CommitHead | None:
    repo = get_repo(repo_source, github_repo_url, refresh_remote=refresh_remote)
    commit = next(repo.iter_commits(max_count=1), None)
    if commit is None:
        return None
    return {
        "sha": commit.hexsha,
        "short_sha": commit.hexsha[:7],
        "subject": commit.summary,
        "author": str(commit.author),
        "date": commit.committed_datetime.isoformat(),
    }


def has_more_commits(
    limit: int = DEFAULT_COMMIT_LIST_LIMIT,
    repo_source: Literal["local", "github"] = "local",
    github_repo_url: str | None = None,
    refresh_remote: bool = True,
) -> bool:
    return get_commit_list_snapshot(
        limit=limit,
        repo_source=repo_source,
        github_repo_url=github_repo_url,
        refresh_remote=refresh_remote,
    )["has_more_commits"]


def count_total_commits(
    repo_source: Literal["local", "github"] = "local",
    github_repo_url: str | None = None,
    refresh_remote: bool = True,
) -> int:
    return get_commit_list_snapshot(
        repo_source=repo_source,
        github_repo_url=github_repo_url,
        refresh_remote=refresh_remote,
    )["total_commit_count"]


def get_commit_list_snapshot(
    limit: int = DEFAULT_COMMIT_LIST_LIMIT,
    repo_source: Literal["local", "github"] = "local",
    github_repo_url: str | None = None,
    refresh_remote: bool = True,
) -> CommitListSnapshot:
    repo = get_repo(repo_source, github_repo_url, refresh_remote=refresh_remote)
    commits: list[dict[str, str]] = []
    total_commit_count = 0

    for total_commit_count, commit in enumerate(repo.iter_commits(), start=1):
        if len(commits) < limit:
            commits.append(
                {
                    "sha": commit.hexsha,
                    "short_sha": commit.hexsha[:7],
                    "subject": commit.summary,
                    "author": str(commit.author),
                    "date": commit.committed_datetime.isoformat(),
                }
            )

    return {
        "commits": commits,
        "has_more_commits": total_commit_count > limit,
        "total_commit_count": total_commit_count,
    }


def build_changed_files_summary(commit) -> str:
    stats = commit.stats.files
    if not stats:
        return "No changed files."

    lines = []
    for path, stat in stats.items():
        lines.append(
            f"{path} | +{stat['insertions']} -{stat['deletions']} "
            f"(lines changed: {stat['lines']})"
        )
    return "\n".join(lines)


def get_file_content_at_commit(repo: Repo, commit_sha: str, path: str) -> str:
    commit = repo.commit(commit_sha)
    blob = commit.tree / path
    return blob.data_stream.read().decode("utf-8", errors="replace")


def get_file_content_at_commit_or_empty(repo: Repo, commit_sha: str | None, path: str) -> str:
    if not commit_sha:
        return ""
    try:
        return get_file_content_at_commit(repo, commit_sha, path)
    except Exception:
        return ""


def list_commit_tree_files(repo: Repo, commit_sha: str) -> list[str]:
    commit = repo.commit(commit_sha)
    paths: list[str] = []
    for item in commit.tree.traverse():
        if item.type == "blob":
            paths.append(item.path)
    return sorted(paths)


def get_commit_parent_sha(repo: Repo, commit_sha: str) -> str | None:
    commit = repo.commit(commit_sha)
    if not commit.parents:
        return None
    return commit.parents[0].hexsha


def detect_code_language(path: str) -> str:
    suffix = PurePath(path).suffix.lower()
    return {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".jsx": "jsx",
        ".java": "java",
        ".kt": "kotlin",
        ".go": "go",
        ".rs": "rust",
        ".rb": "ruby",
        ".php": "php",
        ".swift": "swift",
        ".c": "c",
        ".cpp": "cpp",
        ".cc": "cpp",
        ".h": "c",
        ".hpp": "cpp",
        ".cs": "csharp",
        ".scala": "scala",
        ".sql": "sql",
        ".sh": "bash",
        ".zsh": "bash",
        ".md": "markdown",
        ".json": "json",
        ".yml": "yaml",
        ".yaml": "yaml",
        ".toml": "toml",
        ".html": "html",
        ".css": "css",
        ".xml": "xml",
    }.get(suffix, "")


def format_file_context_block(path: str, content: str) -> str:
    language = detect_code_language(path)
    snippet = content[:MAX_FILE_SNIPPET_CHARS].rstrip()
    return "\n".join(
        [
            f"FILE: {path}",
            f"```{language}",
            snippet,
            "```",
        ]
    )


def extract_patch_text(commit) -> str:
    if commit.parents:
        diff_index = commit.parents[0].diff(commit, create_patch=True)
    else:
        diff_index = commit.diff(NULL_TREE, create_patch=True)
    return build_patch_text_from_diff_index(diff_index)


def build_patch_text_from_diff_index(diff_index) -> str:
    patches: list[str] = []

    for diff in diff_index:
        patch_bytes = diff.diff
        if not patch_bytes:
            continue
        if isinstance(patch_bytes, bytes):
            patch_text = patch_bytes.decode("utf-8", errors="replace")
        else:
            patch_text = str(patch_bytes)

        old_path = diff.a_path or "/dev/null"
        new_path = diff.b_path or "/dev/null"
        patches.append(
            "\n".join(
                [
                    f"diff --git a/{old_path} b/{new_path}",
                    patch_text.strip(),
                ]
            ).strip()
        )

    return "\n\n".join(patches)


def extract_range_patch_text(base_commit, target_commit) -> str:
    diff_index = (
        target_commit.diff(NULL_TREE, create_patch=True)
        if base_commit is NULL_TREE
        else base_commit.diff(target_commit, create_patch=True)
    )
    return build_patch_text_from_diff_index(diff_index)


def get_changed_file_paths(commit) -> list[str]:
    if commit.parents:
        diff_index = commit.parents[0].diff(commit, create_patch=False)
    else:
        diff_index = commit.diff(NULL_TREE, create_patch=False)
    return get_changed_file_paths_from_diff_index(diff_index)


def get_changed_file_paths_from_diff_index(diff_index) -> list[str]:
    paths: list[str] = []
    for diff in diff_index:
        path = diff.b_path or diff.a_path
        if not path:
            continue
        if path not in paths:
            paths.append(path)
    return paths


def get_range_changed_file_paths(base_commit, target_commit) -> list[str]:
    diff_index = (
        target_commit.diff(NULL_TREE, create_patch=False)
        if base_commit is NULL_TREE
        else base_commit.diff(target_commit, create_patch=False)
    )
    return get_changed_file_paths_from_diff_index(diff_index)


def build_file_context_text(commit, repo: Repo) -> str:
    file_contexts: list[str] = []

    for path in get_changed_file_paths(commit)[:MAX_FILE_CONTEXT_FILES]:
        try:
            content = get_file_content_at_commit(repo, commit.hexsha, path)
        except Exception:
            continue

        file_contexts.append(format_file_context_block(path, content))

    combined = "\n\n".join(file_contexts)
    return combined[:MAX_FILE_CONTEXT_CHARS].strip()


def build_range_file_context_text(base_commit, target_commit, repo: Repo) -> str:
    file_contexts: list[str] = []

    for path in get_range_changed_file_paths(base_commit, target_commit)[
        :MAX_FILE_CONTEXT_FILES
    ]:
        try:
            content = get_file_content_at_commit(repo, target_commit.hexsha, path)
        except Exception:
            continue

        file_contexts.append(format_file_context_block(path, content))

    combined = "\n\n".join(file_contexts)
    return combined[:MAX_FILE_CONTEXT_CHARS].strip()


def build_commit_context(commit, selected_reason: str, repo: Repo) -> dict[str, str]:
    return {
        "commit_sha": commit.hexsha,
        "commit_subject": commit.summary,
        "commit_author": str(commit.author),
        "commit_date": commit.committed_datetime.isoformat(),
        "changed_files_summary": build_changed_files_summary(commit),
        "diff_text": sanitize_diff(extract_patch_text(commit)),
        "file_context_text": build_file_context_text(commit, repo),
        "selected_reason": selected_reason,
    }


def build_range_changed_files_summary(base_commit, target_commit) -> str:
    diff_index = (
        target_commit.diff(NULL_TREE, create_patch=False)
        if base_commit is NULL_TREE
        else base_commit.diff(target_commit, create_patch=False)
    )
    lines: list[str] = []
    for diff in diff_index:
        path = diff.b_path or diff.a_path or "unknown"
        change_type = diff.change_type.upper() if diff.change_type else "M"
        lines.append(f"- [{change_type}] {path}")
    return "\n".join(lines)


def build_multi_commit_context(commits, selected_reason: str, repo: Repo) -> dict[str, str]:
    parts: list[dict[str, str]] = [
        build_commit_context(commit, selected_reason, repo) for commit in commits
    ]
    newest_commit = commits[0]
    oldest_commit = commits[-1]
    base_commit = oldest_commit.parents[0] if oldest_commit.parents else NULL_TREE
    range_diff_text = sanitize_diff(extract_range_patch_text(base_commit, newest_commit))
    range_changed_files_summary = build_range_changed_files_summary(
        base_commit, newest_commit
    )
    range_file_context_text = build_range_file_context_text(
        base_commit, newest_commit, repo
    )
    per_commit_changed_files = [
        f"[{part['commit_sha'][:7]}] {part['commit_subject']}\n{part['changed_files_summary']}"
        for part in parts
    ]
    per_commit_file_context = [
        f"# Commit {part['commit_sha'][:7]} - {part['commit_subject']}\n{part['file_context_text']}"
        for part in parts
        if part["file_context_text"]
    ]
    return {
        "commit_sha": ", ".join(part["commit_sha"][:7] for part in parts),
        "commit_subject": " / ".join(part["commit_subject"] for part in parts),
        "commit_author": ", ".join(sorted({part["commit_author"] for part in parts})),
        "commit_date": " ~ ".join([parts[-1]["commit_date"], parts[0]["commit_date"]]),
        "changed_files_summary": "\n\n".join(
            ["[Combined range changed files]", range_changed_files_summary or "No changed files.", "", "[Per-commit breakdown]"]
            + per_commit_changed_files
        ),
        "diff_text": range_diff_text,
        "file_context_text": "\n\n".join(
            [
                "[Combined range file context]",
                range_file_context_text or "No readable changed file content was extracted.",
                "",
                "[Per-commit file context]",
            ]
            + per_commit_file_context
        )[:MAX_FILE_CONTEXT_CHARS].strip(),
        "selected_reason": selected_reason,
    }


def get_commit_by_sha(
    commit_sha: str,
    repo_source: Literal["local", "github"] = "local",
    github_repo_url: str | None = None,
    refresh_remote: bool = True,
):
    repo = get_repo(repo_source, github_repo_url, refresh_remote=refresh_remote)
    return repo.commit(commit_sha)


def get_latest_commit_context(
    commit_mode: Literal["auto", "latest", "selected"] = "auto",
    requested_commit_sha: str | None = None,
    requested_commit_shas: list[str] | None = None,
    repo_source: Literal["local", "github"] = "local",
    github_repo_url: str | None = None,
) -> dict[str, str]:
    repo = get_repo(repo_source, github_repo_url)

    if commit_mode == "selected":
        commit_shas = requested_commit_shas or (
            [requested_commit_sha] if requested_commit_sha else []
        )
        if not commit_shas:
            raise ValueError("selected mode requires at least one commit sha")
        commits = [repo.commit(commit_sha) for commit_sha in commit_shas]
        if len(commits) == 1:
            return build_commit_context(commits[0], "selected_commit", repo)
        return build_multi_commit_context(commits, "selected_commits", repo)

    commits = list(repo.iter_commits(max_count=MAX_COMMITS_TO_SCAN))

    latest_context = build_commit_context(commits[0], "latest", repo)
    if commit_mode == "latest" or latest_context["diff_text"]:
        return latest_context

    for commit in commits[1:]:
        context = build_commit_context(commit, "fallback_recent_text_commit", repo)
        if context["diff_text"]:
            return context

    return latest_context


def get_llm():
    return init_chat_model("openai:gpt-4o-mini")


def collect_commit_context(state: State) -> State:
    repo_source = state.get("repo_source", "local")
    github_repo_url = state.get("github_repo_url")
    commit_mode = state.get("commit_mode", "auto")
    requested_commit_sha = state.get("requested_commit_sha")
    requested_commit_shas = state.get("requested_commit_shas")
    return get_latest_commit_context(
        commit_mode,
        requested_commit_sha,
        requested_commit_shas,
        repo_source,
        github_repo_url,
    )


def build_quiz(state: State) -> State:
    user_request = state["messages"][-1].content if state["messages"] else ""
    difficulty = state.get("difficulty", "medium")
    quiz_style = state.get("quiz_style", "mixed")

    if not state["diff_text"]:
        response = AIMessage(
            content=(
                "최근 커밋에서 퀴즈를 만들 만한 텍스트 diff를 찾지 못했습니다.\n\n"
                f"- 커밋: `{state['commit_subject']}` ({state['commit_sha'][:7]})\n"
                f"- 작성자: {state['commit_author']}\n"
                f"- 날짜: {state['commit_date']}\n\n"
                "현재 변경은 바이너리 파일만 포함하거나 코드 hunk가 없는 상태로 보입니다. "
                "텍스트 코드 변경이 있는 커밋을 지정하거나, 직전 몇 개 커밋을 합쳐서 문제를 만들도록 그래프를 확장하면 더 유용해집니다."
            )
        )
        return {"messages": [response]}

    selected_context_note = ""
    if state["selected_reason"] == "fallback_recent_text_commit":
        selected_context_note = (
            "참고: 가장 최근 커밋에는 텍스트 diff가 없어, "
            "가장 가까운 이전 텍스트 커밋을 기준으로 퀴즈를 생성합니다.\n\n"
        )
    elif state["selected_reason"] == "selected_commit":
        selected_context_note = (
            "참고: 사용자가 선택한 특정 커밋을 기준으로 퀴즈를 생성합니다.\n\n"
        )
    elif state["selected_reason"] == "selected_commits":
        selected_context_note = (
            "참고: 사용자가 선택한 여러 커밋의 흐름을 합쳐 퀴즈를 생성합니다.\n\n"
        )

    output_mode = "study_session" if quiz_style == "study_session" else "quiz"

    prompt = f"""
You are a senior engineer creating a deep code-study artifact from Git changes.

User request:
{user_request}

{selected_context_note}

Commit metadata:
- SHA: {state["commit_sha"]}
- Subject: {state["commit_subject"]}
- Author: {state["commit_author"]}
- Date: {state["commit_date"]}

Changed files summary:
{state["changed_files_summary"]}

Sanitized textual diff:
{state["diff_text"]}

Changed file full content context:
{state["file_context_text"] or "No readable changed file content was extracted."}

Instructions:
1. Respond in Korean unless the user explicitly requested another language.
2. Base everything only on the commit metadata, diff, and changed-file full content context above.
3. Difficulty should be: {difficulty}
4. Preferred output mode is: {output_mode}
5. Do not create trivia questions that can be answered by spotting a single changed line.
6. Focus on intent, architecture, behavior change, code-reading, trade-offs, and regression risk.
7. Make the learner read code and reason across the overall change, not just memorize patch details.
8. When multiple files are involved, connect them explicitly. If the data is insufficient, say so instead of inventing details.
9. Use markdown headings and fenced code blocks so the result renders cleanly in a markdown viewer.

Output requirements:
- Start with `## 변경 개요` and summarize the overall purpose of the change in 3-5 bullets.
- Then add `## 먼저 볼 코드` with 2-4 key code snippets in fenced code blocks.
- Then add `## 학습 질문`.
- Write exactly 4 questions.
- Across the 4 questions, cover:
  - one intent/purpose question
  - one code-reading question
  - one behavior-change question
  - one risk/regression or design trade-off question
- For each question, include:
  - `### 질문 N`
  - `핵심 코드`
  - a relevant fenced code block
  - `질문`
  - `정답`
  - `해설`
  - `코드 근거`
- The answer and explanation should reference the broader flow of the change whenever possible.
- If quiz_style is `multiple_choice`, make at least 2 of the 4 questions multiple-choice.
- If quiz_style is `short_answer`, make at least 2 of the 4 questions short-answer.
- If quiz_style is `conceptual`, bias toward design intent and trade-offs.
- If quiz_style is `study_session`, make the overall tone feel like a guided code-reading session rather than a test.
- End with `## 이 변화에서 배울 점` and 3 concise bullets.
"""

    response = get_llm().invoke(prompt)
    return {"messages": [response]}


graph_builder = StateGraph(State)
graph_builder.add_node("collect_commit_context", collect_commit_context)
graph_builder.add_node("build_quiz", build_quiz)

graph_builder.add_edge(START, "collect_commit_context")
graph_builder.add_edge("collect_commit_context", "build_quiz")
graph_builder.add_edge("build_quiz", END)

graph = graph_builder.compile(name="commit_diff_quiz")
