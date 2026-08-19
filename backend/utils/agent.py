import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path

from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from utils.tools import (
    ALL_TOOLS,
    DEMO_CODE_BASE,
    current_console_log_cache,
    current_outbound_queue,
    current_pending_tab_requests,
    current_project_dir,
    current_tab_content_cache,
    get_available_tools,
)

SYSTEM_PROMPT = """\
You are an expert coding agent that builds Chrome extensions. You have access to
tools for reading, creating, editing, and searching files, listing directories,
and running terminal commands — all scoped to the current project workspace.

## Chrome Extension Basics
A Manifest V3 Chrome extension typically contains:
- **manifest.json** — declares permissions, content scripts, service worker, popup, etc.
- **background.js / service-worker.js** — runs in the extension's service worker context.
- **content scripts** — injected into web pages (access to the DOM, limited Chrome APIs).
- **popup/** — the small UI shown when the extension icon is clicked.
- **options/** — an optional settings page.
- **sidepanel/** — (optional) a side panel UI.

## Workflow
1. Start by listing the project directory to see what already exists.
2. Read existing files before editing them.
3. Create new files with `create_file`, edit existing ones with `edit_file`.
4. Use `grep_search` to find exact symbols, imports, or patterns across the project.
5. Use `codebase_search` to find code by meaning when you're unsure of exact names.
6. Use `run_terminal_command` to install dependencies (`npm install`), build, or test.
7. After creating or significantly modifying an extension, run `validate_extension`.
8. After validation passes, use `load_extension` to prompt the user to install the
   extension. This shows an install card in the sidepanel with buttons to copy the
   path and open chrome://extensions.

## Tool Usage Guidelines
- **read_file**: Max 250 lines per call. Re-read if you need surrounding context.
- **edit_file**: Use "// ... existing code ..." for unchanged sections. Include enough
  context so the edit location is unambiguous.
- **create_file**: Fails if the file already exists — use edit_file instead.
- **run_terminal_command**: Runs in the project root. Timeout is 30 seconds for
  foreground commands. Use is_background=true for long-running processes.
- **grep_search**: Regex search capped at 50 matches. Preferred over reading when you
  know what to search for.
- **codebase_search**: Graph RAG semantic search. Use when you're unsure of exact
  symbol names and want to find code by concept or meaning. Understands code
  relationships (imports, calls, exports) to surface related context.
- **list_dir**: Quick orientation. Use before diving into files.
- **validate_extension**: Validates the Chrome extension in the project workspace.
  Checks manifest.json (required fields, valid keys, valid permissions), verifies all
  referenced files exist, runs JS syntax checks, and scans for deprecated MV3 APIs.
  **Always run this after creating or significantly modifying an extension.** Fix any
  errors it reports before loading.
- **load_extension**: Prompts the user to install the extension from the project
  workspace into their browser. Shows an install card in the sidepanel with the
  extension path and buttons to copy it and open chrome://extensions. **Always call
  this after validate_extension passes.**

Be concise but thorough. When creating a Chrome extension from scratch, generate a
complete, working manifest.json first, then implement the required scripts.
After building or making significant changes, run `validate_extension` and then
`load_extension` to validate and prompt the user to install the extension.\
"""

CODEBASE_SEARCH_UNAVAILABLE_NOTE = """

## Important Note
The `codebase_search` tool is currently UNAVAILABLE because the semantic code index
is still being built in the background. Use `grep_search` for text-based search in
the meantime. The semantic search will become available automatically once indexing
completes (on your next message).\
"""


class EvolveAgent:
    def __init__(self):
        self._base_llm = init_chat_model(model="gpt-5", model_provider="openai")
        # Full tool lookup dict — used for dispatching tool calls at runtime
        self.all_tools = {t.name: t for t in ALL_TOOLS}

    def _build_messages(
        self,
        history: list[dict],
        codebase_search_available: bool = True,
        active_tabs: list[dict] | None = None,
    ) -> list:
        """Convert dict-based history to LangChain message objects."""
        prompt = SYSTEM_PROMPT
        if not codebase_search_available:
            prompt += CODEBASE_SEARCH_UNAVAILABLE_NOTE

        if active_tabs:
            tab_lines = []
            for tab in active_tabs:
                marker = " (active)" if tab.get("active") else ""
                tab_lines.append(
                    f"- id={tab['id']}  {tab.get('title', '(no title)')}  {tab.get('url', '')}{marker}"
                )
            prompt += (
                "\n\n## User's Open Browser Tabs\n"
                "Use `get_tab_content(tab_id)` to fetch a page's text when needed.\n"
                + "\n".join(tab_lines)
            )

        messages = [SystemMessage(content=prompt)]
        for msg in history:
            if msg["role"] == "user":
                messages.append(HumanMessage(content=msg["content"]))
            elif msg["role"] == "assistant":
                messages.append(AIMessage(content=msg["content"]))
        return messages

    def _set_project_context(self, project_id: str) -> Path:
        """Set the current project workspace directory for tool execution.

        Returns the resolved project directory path.
        """
        project_dir = (DEMO_CODE_BASE / project_id).resolve()
        project_dir.mkdir(parents=True, exist_ok=True)
        current_project_dir.set(project_dir)
        return project_dir

    def _prepare_request(self, project_id: str):
        """Set context, start background build if needed, return (bound_llm, cs_available).

        Determines whether the codebase_search tool should be exposed for this
        request based on graph index readiness.  If the index isn't ready, a
        background build is kicked off automatically.
        """
        from utils.graph_rag import is_index_ready, start_background_build

        project_dir = self._set_project_context(project_id)

        cs_available = is_index_ready(project_dir)
        if not cs_available:
            start_background_build(project_dir)

        tools_list = get_available_tools(include_codebase_search=cs_available)
        bound_llm = self._base_llm.bind_tools(tools_list)

        return bound_llm, cs_available

    async def get_chat_response(self, history: list[dict], project_id: str) -> str:
        """Send conversation history to the LLM, execute any tool calls, and return the final reply."""
        bound_llm, cs_available = self._prepare_request(project_id)
        messages = self._build_messages(history, codebase_search_available=cs_available)

        while True:
            response = await bound_llm.ainvoke(messages)
            messages.append(response)

            if not response.tool_calls:
                return response.content

            for tool_call in response.tool_calls:
                tool = self.all_tools[tool_call["name"]]
                result = await tool.ainvoke(tool_call["args"])
                print(f"[tool:{tool_call['name']}] {result}")
                messages.append(
                    ToolMessage(content=str(result), tool_call_id=tool_call["id"])
                )

    async def stream_chat_response(
        self,
        history: list[dict],
        project_id: str,
        active_tabs: list[dict] | None = None,
        pending_tab_requests: dict | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Stream conversation history to the LLM, execute tool calls, and yield event dicts.

        Yields dicts with a "type" key:
        - {"type": "content", "content": "..."} for text chunks
        - {"type": "tool_start", "name": "...", "args": {...}} when a tool begins
        - {"type": "tool_end", "name": "..."} when a tool finishes
        - {"type": "request_tab_content", ...} when a tool needs browser data
        """
        bound_llm, cs_available = self._prepare_request(project_id)
        messages = self._build_messages(
            history,
            codebase_search_available=cs_available,
            active_tabs=active_tabs,
        )

        # Set up the outbound queue so get_tab_content can push events
        outbound: asyncio.Queue[dict] = asyncio.Queue()
        current_outbound_queue.set(outbound)
        current_tab_content_cache.set({})
        current_console_log_cache.set({})
        if pending_tab_requests is not None:
            current_pending_tab_requests.set(pending_tab_requests)

        while True:
            full_response = None
            async for chunk in bound_llm.astream(messages):
                if full_response is None:
                    full_response = chunk
                else:
                    full_response = full_response + chunk

                if chunk.content:
                    yield {"type": "content", "content": chunk.content}

            if full_response is None:
                return

            messages.append(full_response)

            if not full_response.tool_calls:
                return

            for tool_call in full_response.tool_calls:
                tool = self.all_tools[tool_call["name"]]
                yield {"type": "tool_start", "name": tool_call["name"], "args": tool_call["args"]}

                # Run the tool concurrently with draining the outbound queue,
                # so request_tab_content events are yielded while the tool awaits.
                tool_task = asyncio.create_task(tool.ainvoke(tool_call["args"]))

                while not tool_task.done():
                    # Drain any outbound events the tool pushed (e.g. request_tab_content)
                    while not outbound.empty():
                        yield outbound.get_nowait()
                    # Give the tool a chance to make progress
                    await asyncio.sleep(0.05)

                result = tool_task.result()
                # Drain any remaining events
                while not outbound.empty():
                    yield outbound.get_nowait()

                print(f"[tool:{tool_call['name']}] {result}")
                yield {"type": "tool_end", "name": tool_call["name"]}
                messages.append(
                    ToolMessage(content=str(result), tool_call_id=tool_call["id"])
                )

            # All tool calls done; LLM will process results next iteration
            yield {"type": "thinking"}
