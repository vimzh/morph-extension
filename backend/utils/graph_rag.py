"""
Graph RAG-based semantic code search.

Builds a knowledge graph of code entities (files, functions, classes, exports)
and relationships (imports, calls, contains), embeds code chunks, and retrieves
results via vector similarity + graph traversal.
"""

import asyncio
import fnmatch
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Optional

import networkx as nx
import numpy as np

from utils.config import current_provider, get_embedding_model, get_secondary_client, get_secondary_model

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CHUNK_SIZE = 60  # lines per chunk
CHUNK_OVERLAP = 10  # overlap between consecutive chunks
TOP_K_VECTOR = 10  # initial vector search candidates
TOP_K_FINAL = 5  # results returned after graph re-ranking
GRAPH_HOP_DEPTH = 2  # hops to traverse in graph expansion
GRAPH_BONUS_WEIGHT = 0.25  # weight for graph proximity bonus

# File/dir names to skip during indexing
SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "dist", "build",
    ".next", ".cache", "coverage", ".turbo",
}

SKIP_EXTENSIONS = {
    ".lock", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
    ".woff", ".woff2", ".ttf", ".eot", ".map", ".min.js", ".min.css",
    ".pyc", ".pyo", ".so", ".dylib", ".dll", ".exe", ".bin",
}

# Extensions we index
CODE_EXTENSIONS = {
    ".js", ".ts", ".jsx", ".tsx", ".py", ".html", ".css", ".json",
    ".md", ".yaml", ".yml", ".toml", ".sh", ".mjs", ".cjs", ".vue",
    ".svelte", ".scss", ".less",
}

# ---------------------------------------------------------------------------
# Entity extraction prompt (structured output)
# ---------------------------------------------------------------------------

EXTRACT_PROMPT = """\
You are a code analysis tool. Given a source file, extract key entities and relationships.

Return a JSON object with this schema:
{
  "entities": [
    {"name": "entityName", "type": "function|class|variable|export|component", "line": 1}
  ],
  "relationships": [
    {"source": "THIS_FILE", "target": "./path_or_module", "type": "IMPORTS"},
    {"source": "funcA", "target": "funcB", "type": "CALLS"}
  ]
}

Relationship types: IMPORTS, CALLS, EXPORTS, EXTENDS, USES
- For imports: source="THIS_FILE", target=module path.
- For calls between functions in the same file: use function names.
- For exports: source="THIS_FILE", target=exported name.
- Only include significant entities (skip trivial local variables).
- Return ONLY valid JSON. No markdown fences. No explanation."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _should_skip_path(path: Path, project_dir: Path) -> bool:
    """Check if a file/directory should be skipped during indexing."""
    rel = path.relative_to(project_dir)
    for part in rel.parts:
        if part in SKIP_DIRS or part.startswith("."):
            return True
    if path.suffix in SKIP_EXTENSIONS:
        return True
    return False


def _chunk_file(
    content: str,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> list[dict]:
    """Split file content into overlapping line-based chunks."""
    lines = content.splitlines()
    total = len(lines)
    if total == 0:
        return []

    chunks = []
    start = 0
    while start < total:
        end = min(start + chunk_size, total)
        chunk_lines = lines[start:end]
        chunks.append(
            {
                "start_line": start + 1,  # 1-indexed
                "end_line": end,
                "content": "\n".join(chunk_lines),
            }
        )
        if end >= total:
            break
        start += chunk_size - overlap

    return chunks


def _cosine_similarity_batch(
    query: np.ndarray, matrix: np.ndarray
) -> np.ndarray:
    """Cosine similarity between a query vector and a matrix of vectors."""
    norms = np.linalg.norm(matrix, axis=1)
    query_norm = np.linalg.norm(query)
    # Avoid division by zero
    safe_norms = np.where(norms == 0, 1.0, norms)
    safe_query_norm = max(query_norm, 1e-10)
    return matrix @ query / (safe_norms * safe_query_norm)


def _compute_project_fingerprint(project_dir: Path) -> str:
    """Hash of all indexable file paths + mtimes to detect changes."""
    hasher = hashlib.md5()
    for path in sorted(project_dir.rglob("*")):
        if path.is_file() and not _should_skip_path(path, project_dir):
            if path.suffix in CODE_EXTENSIONS:
                hasher.update(f"{path}:{path.stat().st_mtime_ns}".encode())
    return hasher.hexdigest()


# ---------------------------------------------------------------------------
# CodeGraphIndex
# ---------------------------------------------------------------------------


class CodeGraphIndex:
    """In-memory Graph RAG index for a single project."""

    def __init__(self, provider: str = "openai") -> None:
        self.graph: nx.DiGraph = nx.DiGraph()
        # Parallel arrays for fast batch cosine similarity
        self._chunk_ids: list[str] = []
        self._embedding_matrix: np.ndarray | None = None
        self.fingerprint: str = ""
        self.built_at: float = 0.0
        self._provider = provider

    # -- Embedding ---------------------------------------------------------

    async def _embed_texts(self, texts: list[str]) -> list[np.ndarray]:
        """Embed a batch of texts (batched in groups of 100)."""
        if not texts:
            return []
        client = get_secondary_client(self._provider)
        embedding_model = get_embedding_model(self._provider)
        all_embeddings: list[np.ndarray] = []
        for i in range(0, len(texts), 100):
            batch = texts[i : i + 100]
            resp = await client.embeddings.create(model=embedding_model, input=batch)
            all_embeddings.extend(np.array(d.embedding) for d in resp.data)
        return all_embeddings

    # -- Entity extraction -------------------------------------------------

    async def _extract_entities(self, file_path: str, content: str) -> dict:
        """Use an LLM to extract entities and relationships from a source file."""
        try:
            client = get_secondary_client(self._provider)
            model = get_secondary_model(self._provider)
            # Truncate large files to stay within token budget
            truncated = content[:8000] if len(content) > 8000 else content
            resp = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": EXTRACT_PROMPT},
                    {
                        "role": "user",
                        "content": f"File: {file_path}\n\n```\n{truncated}\n```",
                    },
                ],
                temperature=0,
                response_format={"type": "json_object"},
            )
            raw = resp.choices[0].message.content
            if raw:
                return json.loads(raw)
        except Exception as e:
            logger.warning("Entity extraction failed for %s: %s", file_path, e)
        return {"entities": [], "relationships": []}

    # -- Build index -------------------------------------------------------

    async def build_index(self, project_dir: Path) -> dict:
        """Build the full graph index. Returns stats dict."""
        logger.info("Building Graph RAG index for %s ...", project_dir)
        t0 = time.time()

        self.graph.clear()
        self._chunk_ids = []
        self._embedding_matrix = None

        # ---- Collect indexable files ----
        files: list[tuple[str, str]] = []  # (rel_path, content)
        for path in sorted(project_dir.rglob("*")):
            if not path.is_file():
                continue
            if _should_skip_path(path, project_dir):
                continue
            if path.suffix not in CODE_EXTENSIONS:
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
                if not content.strip():
                    continue
                rel_path = str(path.relative_to(project_dir))
                files.append((rel_path, content))
            except Exception:
                continue

        if not files:
            logger.info("No indexable files found.")
            self.fingerprint = _compute_project_fingerprint(project_dir)
            self.built_at = time.time()
            return {"files": 0, "chunks": 0, "nodes": 0, "edges": 0}

        # ---- Phase 1: Chunk files, create graph nodes ----
        all_chunks: list[dict] = []
        for rel_path, content in files:
            file_node = f"file:{rel_path}"
            self.graph.add_node(file_node, type="file", path=rel_path)

            for chunk in _chunk_file(content):
                node_id = f"chunk:{rel_path}:{chunk['start_line']}-{chunk['end_line']}"
                self.graph.add_node(
                    node_id,
                    type="chunk",
                    path=rel_path,
                    start_line=chunk["start_line"],
                    end_line=chunk["end_line"],
                    content=chunk["content"],
                )
                self.graph.add_edge(file_node, node_id, relation="CONTAINS")
                all_chunks.append(
                    {
                        "node_id": node_id,
                        "file": rel_path,
                        "start_line": chunk["start_line"],
                        "end_line": chunk["end_line"],
                        "content": chunk["content"],
                    }
                )

        # ---- Phase 2: Extract entities & relationships (parallel) ----
        extraction_tasks = [
            self._extract_entities(rel_path, content) for rel_path, content in files
        ]
        results = await asyncio.gather(*extraction_tasks, return_exceptions=True)

        for (rel_path, _content), result in zip(files, results):
            if isinstance(result, BaseException):
                continue
            file_node = f"file:{rel_path}"

            # Entity nodes
            for ent in result.get("entities", []):
                ent_name = ent.get("name", "")
                if not ent_name:
                    continue
                ent_id = f"entity:{rel_path}:{ent_name}"
                self.graph.add_node(
                    ent_id,
                    type="entity",
                    entity_type=ent.get("type", "unknown"),
                    name=ent_name,
                    path=rel_path,
                    line=ent.get("line", 0),
                )
                self.graph.add_edge(file_node, ent_id, relation="DEFINES")

            # Relationship edges
            for rel in result.get("relationships", []):
                src = rel.get("source", "")
                tgt = rel.get("target", "")
                rel_type = rel.get("type", "RELATED")
                if not src or not tgt:
                    continue

                # Resolve source
                src_id = (
                    file_node
                    if src == "THIS_FILE"
                    else f"entity:{rel_path}:{src}"
                )

                # Resolve target
                if tgt.startswith("./") or tgt.startswith("../"):
                    # Relative file import — normalise
                    tgt_id = f"file:{tgt.lstrip('./')}"
                else:
                    # Could be a same-file entity or an external module
                    candidate = f"entity:{rel_path}:{tgt}"
                    if self.graph.has_node(candidate):
                        tgt_id = candidate
                    else:
                        tgt_id = f"ref:{tgt}"
                        if not self.graph.has_node(tgt_id):
                            self.graph.add_node(tgt_id, type="reference", name=tgt)

                if not self.graph.has_node(src_id):
                    self.graph.add_node(
                        src_id, type="entity", name=src, path=rel_path
                    )

                self.graph.add_edge(src_id, tgt_id, relation=rel_type)

        # ---- Phase 3: Embed all chunks ----
        chunk_texts = [
            f"# {c['file']}  (lines {c['start_line']}-{c['end_line']})\n{c['content']}"
            for c in all_chunks
        ]
        embeddings = await self._embed_texts(chunk_texts)

        self._chunk_ids = [c["node_id"] for c in all_chunks]
        self._embedding_matrix = np.array(embeddings) if embeddings else None

        self.fingerprint = _compute_project_fingerprint(project_dir)
        self.built_at = time.time()

        stats = {
            "files": len(files),
            "chunks": len(all_chunks),
            "nodes": self.graph.number_of_nodes(),
            "edges": self.graph.number_of_edges(),
            "time_s": round(time.time() - t0, 1),
        }
        logger.info(
            "Graph RAG index built: %d files, %d chunks, %d nodes, %d edges in %.1fs",
            stats["files"],
            stats["chunks"],
            stats["nodes"],
            stats["edges"],
            stats["time_s"],
        )
        return stats

    # -- Search ------------------------------------------------------------

    async def search(
        self,
        query: str,
        target_directories: Optional[list[str]] = None,
        top_k: int = TOP_K_FINAL,
    ) -> list[dict]:
        """Semantic search with graph-enhanced re-ranking.

        Returns list of dicts:
          file, start_line, end_line, content, score, entities, related
        """
        if self._embedding_matrix is None or len(self._chunk_ids) == 0:
            return []

        # Embed the query
        query_vecs = await self._embed_texts([query])
        if not query_vecs:
            return []
        query_vec = query_vecs[0]

        # ---- Vector similarity (fast batch) ----
        sims = _cosine_similarity_batch(query_vec, self._embedding_matrix)

        # Build id -> score mapping, optionally filtered by directory
        vec_scores: dict[str, float] = {}
        for idx, node_id in enumerate(self._chunk_ids):
            if target_directories:
                path = self.graph.nodes[node_id].get("path", "")
                if not any(
                    path.startswith(d.strip("/")) or fnmatch.fnmatch(path, d)
                    for d in target_directories
                ):
                    continue
            vec_scores[node_id] = float(sims[idx])

        if not vec_scores:
            return []

        # ---- Graph expansion ----
        # Take top vector hits and boost their graph neighbours
        sorted_vec = sorted(vec_scores.items(), key=lambda x: x[1], reverse=True)
        top_hits = sorted_vec[:TOP_K_VECTOR]

        graph_bonus: dict[str, float] = {}
        for node_id, v_score in top_hits:
            file_path = self.graph.nodes[node_id].get("path", "")
            file_node = f"file:{file_path}"
            if not self.graph.has_node(file_node):
                continue

            # BFS from file node — find related files/chunks within hops
            try:
                # Use undirected view so we can traverse both directions
                undirected = self.graph.to_undirected(as_view=True)
                neighbours = nx.single_source_shortest_path_length(
                    undirected, file_node, cutoff=GRAPH_HOP_DEPTH
                )
            except nx.NetworkXError:
                continue

            for neighbour_id, distance in neighbours.items():
                if not neighbour_id.startswith("chunk:"):
                    continue
                if neighbour_id == node_id:
                    continue
                # Bonus decays with graph distance, proportional to vector hit score
                bonus = v_score * (1.0 / (1 + distance))
                graph_bonus[neighbour_id] = max(
                    graph_bonus.get(neighbour_id, 0.0), bonus
                )

        # ---- Combine scores ----
        final_scores: dict[str, float] = {}
        for node_id, v_score in vec_scores.items():
            g_bonus = graph_bonus.get(node_id, 0.0)
            final_scores[node_id] = v_score + GRAPH_BONUS_WEIGHT * g_bonus

        # ---- Rank and build results ----
        ranked = sorted(final_scores.items(), key=lambda x: x[1], reverse=True)[
            :top_k
        ]

        results: list[dict] = []
        for node_id, score in ranked:
            nd = self.graph.nodes[node_id]
            chunk_path = nd.get("path", "")
            chunk_start = nd.get("start_line", 0)
            chunk_end = nd.get("end_line", 0)

            # Collect entities defined in this chunk's line range
            entity_labels: list[str] = []
            for n, d in self.graph.nodes(data=True):
                if (
                    d.get("type") == "entity"
                    and d.get("path") == chunk_path
                    and chunk_start <= d.get("line", 0) <= chunk_end
                ):
                    entity_labels.append(
                        f"{d.get('entity_type', '?')}:{d.get('name', '?')}"
                    )

            # Collect file-level relationships for context
            file_node = f"file:{chunk_path}"
            related_labels: list[str] = []
            if self.graph.has_node(file_node):
                for _src, tgt, edata in self.graph.edges(file_node, data=True):
                    rel_name = edata.get("relation", "")
                    if rel_name in ("IMPORTS", "EXPORTS"):
                        tgt_data = self.graph.nodes.get(tgt, {})
                        name = tgt_data.get("name", tgt_data.get("path", tgt))
                        related_labels.append(f"{rel_name} {name}")

            results.append(
                {
                    "file": chunk_path,
                    "start_line": chunk_start,
                    "end_line": chunk_end,
                    "content": nd.get("content", ""),
                    "score": round(score, 4),
                    "entities": entity_labels,
                    "related": related_labels[:6],
                }
            )

        return results


# ---------------------------------------------------------------------------
# Global index cache  (project_dir_str -> CodeGraphIndex)
# ---------------------------------------------------------------------------

_index_cache: dict[str, CodeGraphIndex] = {}
_build_in_progress: set[str] = set()


async def get_or_build_index(project_dir: Path, provider: str | None = None) -> CodeGraphIndex:
    """Return cached index, rebuilding only when project files have changed."""
    if provider is None:
        provider = current_provider.get("openai")
    key = str(project_dir)
    current_fp = _compute_project_fingerprint(project_dir)

    cached = _index_cache.get(key)
    if cached is not None and cached.fingerprint == current_fp:
        logger.info("Graph RAG index cache hit for %s", project_dir)
        return cached

    index = CodeGraphIndex(provider=provider)
    await index.build_index(project_dir)
    _index_cache[key] = index
    return index


# ---------------------------------------------------------------------------
# Background build helpers
# ---------------------------------------------------------------------------


def is_index_ready(project_dir: Path) -> bool:
    """Return True if the index is cached and up-to-date (non-blocking)."""
    key = str(project_dir)
    cached = _index_cache.get(key)
    if cached is None:
        return False
    current_fp = _compute_project_fingerprint(project_dir)
    return cached.fingerprint == current_fp


def is_index_building(project_dir: Path) -> bool:
    """Return True if a background build is currently in progress."""
    return str(project_dir) in _build_in_progress


def start_background_build(project_dir: Path, provider: str | None = None) -> None:
    """Kick off a background index build if not already built or building.

    Safe to call from any async context — the build runs as a fire-and-forget
    asyncio task.
    """
    if provider is None:
        provider = current_provider.get("openai")
    key = str(project_dir)
    if is_index_ready(project_dir) or key in _build_in_progress:
        return

    _build_in_progress.add(key)

    async def _do_build() -> None:
        try:
            index = CodeGraphIndex(provider=provider)
            await index.build_index(project_dir)
            _index_cache[key] = index
            logger.info("Background graph index build complete for %s", project_dir)
        except Exception as e:
            logger.error(
                "Background graph index build failed for %s: %s", project_dir, e
            )
        finally:
            _build_in_progress.discard(key)

    asyncio.create_task(_do_build())
