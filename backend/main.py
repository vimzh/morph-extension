import asyncio
import json
import logging
import shutil
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from openai import AsyncOpenAI
from pydantic import BaseModel

from utils.agent import EvolveAgent
from utils.db import (
    create_conversation,
    create_project,
    delete_project,
    get_history,
    get_messages,
    init_db,
    list_conversations as db_list_conversations,
    list_projects as db_list_projects,
    save_message,
    update_conversation_title,
)
from utils.tools import DEMO_CODE_BASE

logger = logging.getLogger(__name__)

_openai_client = AsyncOpenAI()
agent = EvolveAgent()


async def generate_conversation_title(user_message: str, assistant_message: str) -> str:
    """Generate a short conversation title from the first message exchange."""
    response = await _openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "Generate a concise 3-6 word title for this conversation. "
                    "Return only the title text, nothing else. No quotes or punctuation at the end."
                ),
            },
            {
                "role": "user",
                "content": f"User: {user_message[:500]}\n\nAssistant: {assistant_message[:500]}",
            },
        ],
        max_tokens=20,
        temperature=0.5,
    )
    return response.choices[0].message.content.strip()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    DEMO_CODE_BASE.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Models ---


class CreateProjectRequest(BaseModel):
    name: str


class Project(BaseModel):
    id: str
    name: str
    created_at: str


class ChatRequest(BaseModel):
    query: str
    project_id: str
    conversation_id: str | None = None


class ChatResponse(BaseModel):
    message: str
    conversation_id: str


class Conversation(BaseModel):
    id: str
    title: str | None = None
    created_at: str


class Message(BaseModel):
    role: str
    content: str
    created_at: str


# --- Project Routes ---


@app.post("/projects", response_model=Project)
async def create_project_route(request: CreateProjectRequest):
    project_id, created_at = await create_project(request.name)
    (DEMO_CODE_BASE / project_id).mkdir(parents=True, exist_ok=True)
    return Project(id=project_id, name=request.name, created_at=created_at)


@app.get("/projects", response_model=list[Project])
async def list_projects():
    rows = await db_list_projects()
    return [Project(**r) for r in rows]


@app.delete("/projects/{project_id}")
async def delete_project_route(project_id: str):
    deleted = await delete_project(project_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Project not found")
    workspace = DEMO_CODE_BASE / project_id
    if workspace.exists():
        shutil.rmtree(workspace)
    return {"ok": True}


# --- Chat Routes ---


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    if request.conversation_id:
        conv_id = request.conversation_id
    else:
        conv_id, _ = await create_conversation(request.project_id)

    await save_message(conv_id, "user", request.query)

    history = await get_history(conv_id)
    assistant_msg = await agent.get_chat_response(history, project_id=request.project_id)

    await save_message(conv_id, "assistant", assistant_msg)

    # Generate title for new conversations
    if not request.conversation_id:
        try:
            title = await generate_conversation_title(request.query, assistant_msg)
            await update_conversation_title(conv_id, title)
        except Exception:
            logger.exception("Failed to generate conversation title")

    return ChatResponse(message=assistant_msg, conversation_id=conv_id)


@app.websocket("/ws/{project_id}")
async def ws_chat(websocket: WebSocket, project_id: str):
    await websocket.accept()

    # Shared across all chat turns on this connection
    pending_tab_requests: dict[str, asyncio.Future] = {}
    # Queue for incoming FE messages (tab_content_response, etc.) while the
    # agent is streaming.  A background listener task fills this queue.
    incoming: asyncio.Queue[dict] = asyncio.Queue()

    async def _listen_for_responses():
        """Read WS messages and route them: chat messages go on `incoming`,
        tab_content_response resolves the matching Future directly."""
        try:
            while True:
                data = await websocket.receive_json()
                msg_type = data.get("type")

                if msg_type == "tab_content_response":
                    rid = data.get("request_id")
                    content = data.get("content", "")
                    fut = pending_tab_requests.pop(rid, None)
                    if fut and not fut.done():
                        fut.set_result(content)
                else:
                    await incoming.put(data)
        except WebSocketDisconnect:
            await incoming.put({"type": "_disconnect"})

    listener = asyncio.create_task(_listen_for_responses())

    try:
        while True:
            data = await incoming.get()
            if data.get("type") == "_disconnect":
                break
            if data.get("type") != "chat":
                continue

            query = data["query"]
            conversation_id = data.get("conversation_id")
            active_tabs = data.get("active_tabs")

            if conversation_id:
                conv_id = conversation_id
            else:
                conv_id, _ = await create_conversation(project_id)

            await save_message(conv_id, "user", query)
            history = await get_history(conv_id)

            # Let the client know the conversation id first
            await websocket.send_json(
                {"type": "conversation_id", "conversation_id": conv_id}
            )

            collected: list[str] = []
            try:
                async for event in agent.stream_chat_response(
                    history,
                    project_id=project_id,
                    active_tabs=active_tabs,
                    pending_tab_requests=pending_tab_requests,
                ):
                    if event["type"] == "content":
                        collected.append(event["content"])
                    await websocket.send_json(event)

                content = "".join(collected)
                await save_message(conv_id, "assistant", content)
                await websocket.send_json(
                    {
                        "type": "done",
                        "conversation_id": conv_id,
                        "content": content,
                    }
                )

                # Generate a title for new conversations
                if not conversation_id:

                    async def _generate_and_send_title(
                        ws: WebSocket, cid: str, user_msg: str, asst_msg: str
                    ):
                        try:
                            title = await generate_conversation_title(user_msg, asst_msg)
                            await update_conversation_title(cid, title)
                            await ws.send_json(
                                {
                                    "type": "conversation_title",
                                    "conversation_id": cid,
                                    "title": title,
                                }
                            )
                        except Exception:
                            logger.exception("Failed to generate conversation title")

                    asyncio.create_task(
                        _generate_and_send_title(websocket, conv_id, query, content)
                    )
            except WebSocketDisconnect:
                break
            except Exception as exc:
                logger.exception("Error during agent streaming")
                try:
                    await websocket.send_json(
                        {"type": "error", "message": str(exc)}
                    )
                except Exception:
                    break
    finally:
        listener.cancel()


# --- Conversation Routes ---


@app.get("/projects/{project_id}/conversations", response_model=list[Conversation])
async def list_conversations(project_id: str):
    rows = await db_list_conversations(project_id)
    return [Conversation(**r) for r in rows]


@app.get("/conversations/{conversation_id}", response_model=list[Message])
async def get_conversation(conversation_id: str):
    rows = await get_messages(conversation_id)
    return [Message(**r) for r in rows]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
