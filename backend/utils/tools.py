import asyncio
import contextvars
import logging
import subprocess
import uuid
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool
from openai import AsyncOpenAI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Context variable holding the current project workspace path (set per-request)
current_project_dir: contextvars.ContextVar[Path] = contextvars.ContextVar(
    "current_project_dir"
)

# Context variable holding a dict of pending tab-content requests for the current
# WS connection.  The agent's streaming loop checks for outbound events on this
# queue so the WS handler can forward them to the browser.
current_pending_tab_requests: contextvars.ContextVar[
    dict[str, asyncio.Future]
] = contextvars.ContextVar("current_pending_tab_requests")

# Queue for outbound events the tool needs to push to the client (e.g.
# request_tab_content).  The agent yields these alongside its own events.
current_outbound_queue: contextvars.ContextVar[
    asyncio.Queue[dict]
] = contextvars.ContextVar("current_outbound_queue")

# Per-session cache of full tab content so the agent can paginate through it
# without re-fetching from the browser on every call.
# Key: (tab_id, include_html) → full content string
current_tab_content_cache: contextvars.ContextVar[
    dict[tuple[int, bool], str]
] = contextvars.ContextVar("current_tab_content_cache")

TAB_CONTENT_CHUNK_SIZE = 10_000

DEMO_CODE_BASE = Path(__file__).parent.parent / "demo_code"


def _resolve_path(relative_path: str) -> Path:
    """Resolve a relative path within the current project workspace.

    Raises ValueError if the resolved path escapes the project directory.
    """
    project_dir = current_project_dir.get()
    resolved = (project_dir / relative_path).resolve()
    if not str(resolved).startswith(str(project_dir.resolve())):
        raise ValueError(f"Path '{relative_path}' escapes the project workspace")
    return resolved


# ---------------------------------------------------------------------------
# Tool 1: list_dir
# ---------------------------------------------------------------------------


@tool
async def list_dir(relative_workspace_path: str = ".") -> str:
    """List the contents of a directory in the project workspace.

    Use this to understand project structure before searching or reading files.

    Args:
        relative_workspace_path: Directory path relative to the project root. Defaults to "." for the project root.

    Returns:
        A listing of files and directories.
    """
    try:
        resolved = _resolve_path(relative_workspace_path)
        if not resolved.exists():
            return f"Error: Directory '{relative_workspace_path}' does not exist."
        if not resolved.is_dir():
            return f"Error: '{relative_workspace_path}' is not a directory."

        entries = sorted(resolved.iterdir())
        lines = []
        for entry in entries:
            if entry.name.startswith("."):
                continue
            suffix = "/" if entry.is_dir() else ""
            lines.append(f"{entry.name}{suffix}")

        if not lines:
            return f"Directory '{relative_workspace_path}' is empty."
        return "\n".join(lines)
    except Exception as e:
        return f"Error listing directory: {e}"


# ---------------------------------------------------------------------------
# Tool 2: read_file
# ---------------------------------------------------------------------------


@tool
async def read_file(
    target_file: str,
    should_read_entire_file: bool = False,
    start_line_one_indexed: int = 1,
    end_line_one_indexed_inclusive: int = 250,
) -> str:
    """Read a specific line range from a file in the project workspace (max 250 lines).

    Re-run with a different range if the missing lines might contain required context
    (imports, definitions, etc.).

    Args:
        target_file: Path to the file, relative to the project root.
        should_read_entire_file: If true, read the entire file (only for small files).
        start_line_one_indexed: Start line (1-indexed). Default 1.
        end_line_one_indexed_inclusive: End line (1-indexed, inclusive). Default 250.

    Returns:
        The requested lines with line numbers, plus a summary of what's outside the range.
    """
    try:
        resolved = _resolve_path(target_file)
        if not resolved.exists():
            return f"Error: File '{target_file}' does not exist."
        if not resolved.is_file():
            return f"Error: '{target_file}' is not a file."

        content = resolved.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        total = len(lines)

        if should_read_entire_file:
            start_line_one_indexed = 1
            end_line_one_indexed_inclusive = total

        start = max(1, start_line_one_indexed)
        end = min(total, end_line_one_indexed_inclusive)

        # Enforce 250-line limit unless reading entire file
        if end - start + 1 > 250 and not should_read_entire_file:
            end = start + 249

        selected = lines[start - 1 : end]
        numbered = [f"{i}: {line}" for i, line in enumerate(selected, start=start)]

        result = f"File: {target_file} ({total} lines total)\n"
        if start > 1:
            result += f"... ({start - 1} lines above) ...\n"
        result += "\n".join(numbered)
        if end < total:
            result += f"\n... ({total - end} lines below) ..."

        return result
    except Exception as e:
        return f"Error reading file: {e}"


# ---------------------------------------------------------------------------
# Tool 3: grep_search
# ---------------------------------------------------------------------------


@tool
async def grep_search(
    query: str,
    include_pattern: Optional[str] = None,
    exclude_pattern: Optional[str] = None,
    case_sensitive: bool = True,
) -> str:
    """Fast regex search across files in the project workspace (ripgrep).

    Results are capped at 50 matches. Preferred over broad reading when you know the
    exact string, symbol name, or pattern to find.

    Args:
        query: Regex pattern to search for.
        include_pattern: Glob to include (e.g. "*.ts", "*.json").
        exclude_pattern: Glob to exclude (e.g. "node_modules/**").
        case_sensitive: Whether the search is case-sensitive. Default true.

    Returns:
        Matching lines with file paths and line numbers, capped at 50 results.
    """
    try:
        project_dir = current_project_dir.get()
        cmd = ["rg", "--line-number", "--max-count", "50", "--no-heading"]

        if not case_sensitive:
            cmd.append("--ignore-case")
        if include_pattern:
            cmd.extend(["--glob", include_pattern])
        if exclude_pattern:
            cmd.extend(["--glob", f"!{exclude_pattern}"])

        cmd.append(query)
        cmd.append(".")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(project_dir),
        )

        output = result.stdout.strip()
        if not output:
            return "No matches found."

        lines = output.splitlines()
        if len(lines) >= 50:
            return "\n".join(lines[:50]) + "\n\n(Results capped at 50 matches)"
        return "\n".join(lines)
    except subprocess.TimeoutExpired:
        return "Error: Search timed out after 15 seconds."
    except FileNotFoundError:
        return "Error: ripgrep (rg) is not installed."
    except Exception as e:
        return f"Error searching: {e}"


# ---------------------------------------------------------------------------
# Tool 4: create_file
# ---------------------------------------------------------------------------


@tool
async def create_file(target_file: str, content: str) -> str:
    """Create a new file in the project workspace with the given content.

    Creates intermediate directories as needed. Fails if the file already exists —
    use edit_file to modify existing files.

    Args:
        target_file: Path for the new file, relative to the project root.
        content: The full content to write to the file.

    Returns:
        Success or error message.
    """
    try:
        resolved = _resolve_path(target_file)
        if resolved.exists():
            return (
                f"Error: File '{target_file}' already exists. "
                "Use edit_file to modify it."
            )

        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")

        line_count = len(content.splitlines())
        return f"Successfully created '{target_file}' ({line_count} lines)."
    except Exception as e:
        return f"Error creating file: {e}"


# ---------------------------------------------------------------------------
# Tool 5: edit_file  (uses a secondary LLM to apply the edit)
# ---------------------------------------------------------------------------

EDIT_APPLY_PROMPT = """\
You are a precise code editor. You will receive:
1. The ORIGINAL file content.
2. A short instruction describing the intended change.
3. A CODE EDIT where unchanged sections are represented by comments such as
   "// ... existing code ..." or "# ... existing code ...".

Your task: merge the code edit into the original file and return the COMPLETE
updated file content.

Rules:
- Replace each "... existing code ..." marker with the corresponding original code.
- Apply all changes exactly as described in the code edit.
- Return ONLY the raw file content — no explanations, no markdown fences.
- Preserve exact indentation and formatting of unchanged code."""

_openai_client: AsyncOpenAI | None = None


def _get_openai_client() -> AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = AsyncOpenAI()
    return _openai_client


@tool
async def edit_file(target_file: str, instructions: str, code_edit: str) -> str:
    """Edit an existing file using a secondary model to apply the change.

    Represent unchanged sections with "// ... existing code ..." (or the
    language-appropriate comment style). Include enough surrounding context
    so the change location is unambiguous.

    Args:
        target_file: Path to the file to modify, relative to the project root.
        instructions: One sentence describing the change (guides the applier model).
        code_edit: The minimal edit with "// ... existing code ..." for gaps.

    Returns:
        Success message with a summary, or an error message.
    """
    try:
        resolved = _resolve_path(target_file)
        if not resolved.exists():
            return f"Error: File '{target_file}' does not exist. Use create_file instead."
        if not resolved.is_file():
            return f"Error: '{target_file}' is not a file."

        original_content = resolved.read_text(encoding="utf-8", errors="replace")

        client = _get_openai_client()
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": EDIT_APPLY_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"## Original file: {target_file}\n\n"
                        f"```\n{original_content}\n```\n\n"
                        f"## Instruction\n{instructions}\n\n"
                        f"## Code edit\n```\n{code_edit}\n```"
                    ),
                },
            ],
            temperature=0,
        )

        new_content = response.choices[0].message.content
        if new_content is None:
            return "Error: The applier model returned no content."

        new_content = new_content.strip()

        # Strip markdown fences if the model added them
        if new_content.startswith("```"):
            lines = new_content.splitlines()
            if lines[-1].strip() == "```":
                new_content = "\n".join(lines[1:-1])

        # Ensure trailing newline
        if not new_content.endswith("\n"):
            new_content += "\n"

        resolved.write_text(new_content, encoding="utf-8")

        orig_line_count = len(original_content.splitlines())
        new_line_count = len(new_content.splitlines())
        delta = new_line_count - orig_line_count

        summary = f"Successfully edited '{target_file}'."
        if delta > 0:
            summary += f" (+{delta} lines)"
        elif delta < 0:
            summary += f" ({delta} lines)"

        return summary
    except Exception as e:
        return f"Error editing file: {e}"


# ---------------------------------------------------------------------------
# Tool 6: run_terminal_command
# ---------------------------------------------------------------------------


@tool
async def run_terminal_command(command: str, is_background: bool = False) -> str:
    """Run a terminal command in the project workspace directory.

    Use this to build, test, install dependencies, run scripts, or inspect git state.

    Args:
        command: The terminal command to execute (single line).
        is_background: If true, start in the background and return immediately with the PID.

    Returns:
        Command output (stdout + stderr), or a background process PID message.
    """
    try:
        project_dir = current_project_dir.get()

        if is_background:
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=str(project_dir),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            return f"Background process started (PID {process.pid})."

        process = await asyncio.create_subprocess_shell(
            command,
            cwd=str(project_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=30
            )
        except asyncio.TimeoutError:
            process.kill()
            return "Error: Command timed out after 30 seconds."

        output = ""
        if stdout:
            output += stdout.decode(errors="replace")
        if stderr:
            output += ("\n" if output else "") + stderr.decode(errors="replace")

        if process.returncode != 0:
            output += f"\n(Exit code: {process.returncode})"

        return output.strip() if output.strip() else "(No output)"
    except Exception as e:
        return f"Error running command: {e}"


# ---------------------------------------------------------------------------
# Tool 7: codebase_search  (Graph RAG semantic search)
# ---------------------------------------------------------------------------


@tool
async def codebase_search(
    query: str,
    target_directories: Optional[list[str]] = None,
    explanation: Optional[str] = None,
) -> str:
    """Search the codebase by meaning (semantic search) using Graph RAG.

    Finds the most relevant code snippets by combining vector similarity with
    a code knowledge graph that captures imports, function calls, and exports.
    Use this when you're not sure of the exact symbol/string and want to find
    code related to a concept.

    Args:
        query: Natural-language search text. Prefer using the user's exact wording.
        target_directories: Optional list of directory paths to narrow the search scope.
        explanation: One sentence on why you're using this search (for logging).

    Returns:
        Top matching code snippets with file paths, line numbers, relationship
        context, and relevance scores.
    """
    from utils.graph_rag import get_or_build_index

    try:
        project_dir = current_project_dir.get()

        if explanation:
            logger.info("[codebase_search] %s", explanation)

        index = await get_or_build_index(project_dir)
        results = await index.search(query, target_directories)

        if not results:
            return "No relevant code found for the given query."

        # Format results for the LLM
        parts: list[str] = []
        for i, r in enumerate(results, 1):
            header = f"## Result {i}  (score: {r['score']})  —  {r['file']}:{r['start_line']}-{r['end_line']}"
            meta_parts: list[str] = []
            if r.get("entities"):
                meta_parts.append("Entities: " + ", ".join(r["entities"]))
            if r.get("related"):
                meta_parts.append("Related: " + ", ".join(r["related"]))
            meta = "\n".join(meta_parts)
            code = r["content"]
            parts.append(f"{header}\n{meta}\n```\n{code}\n```")

        return "\n\n".join(parts)
    except Exception as e:
        return f"Error in codebase search: {e}"


# ---------------------------------------------------------------------------
# Tool 8: get_tab_content  (fetches page content from the user's browser)
# ---------------------------------------------------------------------------


@tool
async def get_tab_content(
    tab_id: int, include_html: bool = False, offset: int = 0
) -> str:
    """Fetch the content of a browser tab the user currently has open.

    Use this when you need to inspect what the user is looking at — for example to
    understand the structure of a page they want to build an extension for.  The
    tab_id comes from the active_tabs list provided in the system prompt.

    Results are returned in chunks of ~10 000 characters.  On the first call
    (offset=0) the full page content is fetched from the browser and cached.
    Pass a higher offset to retrieve subsequent chunks.

    Args:
        tab_id: The numeric Chrome tab id to read content from.
        include_html: If True, return the raw HTML of the page body instead of
            just the visible text.  Useful when you need to inspect DOM structure,
            classes, or attributes.  Defaults to False (plain text).
        offset: Character offset to start reading from.  Use 0 (default) to
            fetch fresh content from the browser.  Use the offset indicated in
            the response metadata to continue reading.

    Returns:
        A chunk of the page content together with pagination metadata, or an
        error message if the tab could not be reached.
    """
    try:
        pending = current_pending_tab_requests.get()
        outbound = current_outbound_queue.get()
        cache = current_tab_content_cache.get()
    except LookupError:
        return "Error: get_tab_content is not available outside of a WebSocket session."

    cache_key = (tab_id, include_html)

    # If offset > 0 we expect cached content from a prior offset=0 call.
    if offset > 0:
        full_content = cache.get(cache_key)
        if full_content is None:
            return (
                "Error: No cached content for this tab/mode.  "
                "Call get_tab_content with offset=0 first to fetch the page."
            )
    else:
        # Fetch fresh content from the browser
        request_id = uuid.uuid4().hex
        future: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        pending[request_id] = future

        await outbound.put(
            {
                "type": "request_tab_content",
                "tab_id": tab_id,
                "request_id": request_id,
                "include_html": include_html,
            }
        )

        try:
            full_content = await asyncio.wait_for(future, timeout=15)
        except asyncio.TimeoutError:
            pending.pop(request_id, None)
            return f"Error: Timed out waiting for content from tab {tab_id}."

        # Cache the full content for subsequent paginated reads
        cache[cache_key] = full_content

    # Return the requested chunk
    total = len(full_content)
    chunk = full_content[offset : offset + TAB_CONTENT_CHUNK_SIZE]
    end = offset + len(chunk)
    has_more = end < total

    header = f"[Showing characters {offset}–{end} of {total} total]"
    footer = (
        f"\n\n[More content available — call get_tab_content(tab_id={tab_id}, "
        f"include_html={include_html}, offset={end}) to continue]"
        if has_more
        else "\n\n[End of content]"
    )
    return f"{header}\n\n{chunk}{footer}"


# ---------------------------------------------------------------------------
# Tool 9: validate_extension
# ---------------------------------------------------------------------------


@tool
async def validate_extension() -> str:
    """Validate the Chrome extension in the current project workspace.

    Runs four layers of checks that mirror what Chrome does when loading an
    unpacked extension:

    1. Manifest validation — required fields, valid keys, valid permissions.
    2. File references — every path declared in manifest.json exists on disk.
    3. JavaScript syntax — runs `node --check` on .js files to catch syntax errors.
    4. MV3 compatibility — scans for removed/changed APIs (e.g. chrome.browserAction).

    Call this after creating or significantly modifying an extension to catch
    errors *before* the user tries to load it in Chrome.

    Returns:
        A human-readable validation report listing errors and warnings,
        or a success message if no issues were found.
    """
    from utils.extension_validator import validate_extension as _validate

    try:
        project_dir = current_project_dir.get()
    except LookupError:
        return "Error: No project workspace is set."

    issues = _validate(project_dir)

    if not issues:
        return "Validation passed — no errors or warnings found."

    errors = [i for i in issues if i["level"] == "error"]
    warnings = [i for i in issues if i["level"] == "warning"]

    parts: list[str] = []
    if errors:
        parts.append(f"## Errors ({len(errors)})")
        for e in errors:
            parts.append(f"- [{e['category']}] {e['message']}")
    if warnings:
        parts.append(f"## Warnings ({len(warnings)})")
        for w in warnings:
            parts.append(f"- [{w['category']}] {w['message']}")

    summary = f"Validation found {len(errors)} error(s) and {len(warnings)} warning(s)."
    return summary + "\n\n" + "\n".join(parts)


# ---------------------------------------------------------------------------
# All tools for the agent
# ---------------------------------------------------------------------------

BASE_TOOLS = [list_dir, read_file, grep_search, create_file, edit_file, run_terminal_command, get_tab_content, validate_extension]
ALL_TOOLS = BASE_TOOLS + [codebase_search]


def get_available_tools(include_codebase_search: bool = True) -> list:
    """Return the tool list, optionally excluding codebase_search."""
    if include_codebase_search:
        return list(ALL_TOOLS)
    return list(BASE_TOOLS)