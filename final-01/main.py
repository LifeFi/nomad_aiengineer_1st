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


class State(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
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
    selected_reason: str


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


def get_repo() -> Repo:
    return Repo(".", search_parent_directories=True)


def list_recent_commits(limit: int = DEFAULT_COMMIT_LIST_LIMIT) -> list[dict[str, str]]:
    repo = get_repo()
    commits = []
    for commit in repo.iter_commits(max_count=limit):
        commits.append(
            {
                "sha": commit.hexsha,
                "short_sha": commit.hexsha[:7],
                "subject": commit.summary,
                "author": str(commit.author),
                "date": commit.committed_datetime.isoformat(),
            }
        )
    return commits


def has_more_commits(limit: int = DEFAULT_COMMIT_LIST_LIMIT) -> bool:
    repo = get_repo()
    commits = list(repo.iter_commits(max_count=limit + 1))
    return len(commits) > limit


def count_total_commits() -> int:
    repo = get_repo()
    return sum(1 for _ in repo.iter_commits())


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


def extract_patch_text(commit) -> str:
    if commit.parents:
        diff_index = commit.parents[0].diff(commit, create_patch=True)
    else:
        diff_index = commit.diff(NULL_TREE, create_patch=True)
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


def build_commit_context(commit, selected_reason: str) -> dict[str, str]:
    return {
        "commit_sha": commit.hexsha,
        "commit_subject": commit.summary,
        "commit_author": str(commit.author),
        "commit_date": commit.committed_datetime.isoformat(),
        "changed_files_summary": build_changed_files_summary(commit),
        "diff_text": sanitize_diff(extract_patch_text(commit)),
        "selected_reason": selected_reason,
    }


def build_multi_commit_context(commits, selected_reason: str) -> dict[str, str]:
    parts: list[dict[str, str]] = [
        build_commit_context(commit, selected_reason) for commit in commits
    ]
    return {
        "commit_sha": ", ".join(part["commit_sha"][:7] for part in parts),
        "commit_subject": " / ".join(part["commit_subject"] for part in parts),
        "commit_author": ", ".join(sorted({part["commit_author"] for part in parts})),
        "commit_date": " ~ ".join([parts[-1]["commit_date"], parts[0]["commit_date"]]),
        "changed_files_summary": "\n\n".join(
            [
                f"[{part['commit_sha'][:7]}] {part['commit_subject']}\n{part['changed_files_summary']}"
                for part in parts
            ]
        ),
        "diff_text": sanitize_diff(
            "\n\n".join(
                [
                    f"# Commit {part['commit_sha'][:7]} - {part['commit_subject']}\n{part['diff_text']}"
                    for part in parts
                    if part["diff_text"]
                ]
            )
        ),
        "selected_reason": selected_reason,
    }


def get_commit_by_sha(commit_sha: str):
    return get_repo().commit(commit_sha)


def get_latest_commit_context(
    commit_mode: Literal["auto", "latest", "selected"] = "auto",
    requested_commit_sha: str | None = None,
    requested_commit_shas: list[str] | None = None,
) -> dict[str, str]:
    repo = get_repo()

    if commit_mode == "selected":
        commit_shas = requested_commit_shas or (
            [requested_commit_sha] if requested_commit_sha else []
        )
        if not commit_shas:
            raise ValueError("selected mode requires at least one commit sha")
        commits = [get_commit_by_sha(commit_sha) for commit_sha in commit_shas]
        if len(commits) == 1:
            return build_commit_context(commits[0], "selected_commit")
        return build_multi_commit_context(commits, "selected_commits")

    commits = list(repo.iter_commits(max_count=MAX_COMMITS_TO_SCAN))

    latest_context = build_commit_context(commits[0], "latest")
    if commit_mode == "latest" or latest_context["diff_text"]:
        return latest_context

    for commit in commits[1:]:
        context = build_commit_context(commit, "fallback_recent_text_commit")
        if context["diff_text"]:
            return context

    return latest_context


def get_llm():
    return init_chat_model("openai:gpt-4o-mini")


def collect_commit_context(state: State) -> State:
    commit_mode = state.get("commit_mode", "auto")
    requested_commit_sha = state.get("requested_commit_sha")
    requested_commit_shas = state.get("requested_commit_shas")
    return get_latest_commit_context(
        commit_mode,
        requested_commit_sha,
        requested_commit_shas,
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

    prompt = f"""
You are a senior engineer creating a study quiz from a Git commit diff.

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

Instructions:
1. Respond in Korean unless the user explicitly requested another language.
2. Create 4 quiz questions based only on the commit metadata and diff above.
3. Difficulty should be: {difficulty}
4. Quiz style preference should be: {quiz_style}
5. Mix question styles: at least one conceptual question, one code-reading question, one intent/purpose question, and one risk/regression question.
6. For each question, include:
   - question
   - answer
   - short explanation
7. If the diff does not contain enough context for one question, say that clearly instead of inventing details.
8. End with a short "이 커밋에서 배울 포인트" section with 3 concise bullets.
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
