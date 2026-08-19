import os
import uuid
from datetime import datetime, timezone

import aiosqlite
from dotenv import load_dotenv

load_dotenv()

DB_PATH = "conversations.db"


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                title TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
            )
        """)
        # Migration: add title column to existing databases
        try:
            await db.execute("ALTER TABLE conversations ADD COLUMN title TEXT")
        except Exception:
            pass  # Column already exists
        await db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS rules (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
            )
        """)
        await db.execute("PRAGMA foreign_keys = ON")
        await db.commit()


# --- Projects ---


async def create_project(name: str) -> tuple[str, str]:
    """Create a new project. Returns (project_id, created_at)."""
    project_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO projects (id, name, created_at) VALUES (?, ?, ?)",
            (project_id, name, now),
        )
        await db.commit()
    return project_id, now


async def delete_project(project_id: str) -> bool:
    """Delete a project and all its conversations/messages. Returns True if deleted."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        cursor = await db.execute(
            "DELETE FROM projects WHERE id = ?", (project_id,)
        )
        await db.commit()
        return cursor.rowcount > 0


async def list_projects() -> list[dict]:
    """List all projects, newest first."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, name, created_at FROM projects ORDER BY created_at DESC"
        )
        rows = await cursor.fetchall()
    return [{"id": r[0], "name": r[1], "created_at": r[2]} for r in rows]


# --- Conversations ---


async def create_conversation(project_id: str) -> tuple[str, str]:
    """Create a new conversation within a project. Returns (conversation_id, created_at)."""
    conv_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO conversations (id, project_id, created_at) VALUES (?, ?, ?)",
            (conv_id, project_id, now),
        )
        await db.commit()
    return conv_id, now


async def list_conversations(project_id: str) -> list[dict]:
    """List all conversations for a project, newest first."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, title, created_at FROM conversations WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,),
        )
        rows = await cursor.fetchall()
    return [{"id": r[0], "title": r[1], "created_at": r[2]} for r in rows]


async def update_conversation_title(conversation_id: str, title: str) -> None:
    """Update the title of a conversation."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE conversations SET title = ? WHERE id = ?",
            (title, conversation_id),
        )
        await db.commit()


# --- Messages ---


async def save_message(conversation_id: str, role: str, content: str) -> None:
    """Save a message to the database."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (conversation_id, role, content, now),
        )
        await db.commit()


async def get_history(conversation_id: str) -> list[dict]:
    """Get conversation history as a list of {role, content} dicts."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id",
            (conversation_id,),
        )
        rows = await cursor.fetchall()
    return [{"role": r, "content": c} for r, c in rows]


async def get_messages(conversation_id: str) -> list[dict]:
    """Get all messages for a conversation."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY id",
            (conversation_id,),
        )
        rows = await cursor.fetchall()
    return [{"role": r[0], "content": r[1], "created_at": r[2]} for r in rows]


# --- Rules ---


async def save_rules(project_id: str, rules: list[str]) -> list[dict]:
    """Bulk-insert new rules for a project. Returns the created rule dicts."""
    now = datetime.now(timezone.utc).isoformat()
    created: list[dict] = []
    async with aiosqlite.connect(DB_PATH) as db:
        for content in rules:
            rule_id = uuid.uuid4().hex
            await db.execute(
                "INSERT INTO rules (id, project_id, content, created_at) VALUES (?, ?, ?, ?)",
                (rule_id, project_id, content, now),
            )
            created.append({"id": rule_id, "content": content, "created_at": now})
        await db.commit()
    return created


async def get_rules(project_id: str) -> list[dict]:
    """Get all rules for a project, oldest first."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, content, created_at FROM rules WHERE project_id = ? ORDER BY created_at",
            (project_id,),
        )
        rows = await cursor.fetchall()
    return [{"id": r[0], "content": r[1], "created_at": r[2]} for r in rows]


async def delete_rule(rule_id: str) -> bool:
    """Delete a single rule. Returns True if deleted."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
        await db.commit()
        return cursor.rowcount > 0
