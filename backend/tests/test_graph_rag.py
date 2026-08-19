"""
Test Graph RAG indexing and search against a sample Chrome extension project.

Usage:
    python -m tests.test_graph_rag
"""

import asyncio
import shutil
from pathlib import Path

from utils.graph_rag import get_or_build_index
from utils.tools import DEMO_CODE_BASE, current_project_dir

# ---------------------------------------------------------------------------
# Sample Chrome extension files
# ---------------------------------------------------------------------------

EXTENSION_FILES: dict[str, str] = {
    "manifest.json": """{
  "manifest_version": 3,
  "name": "Tab Manager",
  "version": "1.0.0",
  "description": "A simple Chrome extension to manage and search open tabs.",
  "permissions": ["tabs", "storage"],
  "action": {
    "default_popup": "popup/popup.html",
    "default_icon": "icons/icon48.png"
  },
  "background": {
    "service_worker": "background.js"
  },
  "content_scripts": [
    {
      "matches": ["<all_urls>"],
      "js": ["content/content.js"],
      "css": ["content/content.css"]
    }
  ]
}
""",
    "background.js": """// background.js — Service worker for Tab Manager extension

const TAB_HISTORY_KEY = "tab_history";
const MAX_HISTORY = 100;

// Track tab activations
chrome.tabs.onActivated.addListener(async (activeInfo) => {
  const tab = await chrome.tabs.get(activeInfo.tabId);
  await recordTabVisit(tab);
});

// Track tab updates (URL changes)
chrome.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
  if (changeInfo.status === "complete" && tab.url) {
    await recordTabVisit(tab);
  }
});

async function recordTabVisit(tab) {
  const { tab_history = [] } = await chrome.storage.local.get(TAB_HISTORY_KEY);
  const entry = {
    tabId: tab.id,
    url: tab.url,
    title: tab.title || "",
    timestamp: Date.now(),
  };
  tab_history.unshift(entry);
  if (tab_history.length > MAX_HISTORY) {
    tab_history.length = MAX_HISTORY;
  }
  await chrome.storage.local.set({ [TAB_HISTORY_KEY]: tab_history });
}

// Message handler — responds to popup requests
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type === "GET_TABS") {
    chrome.tabs.query({}, (tabs) => {
      sendResponse({ tabs: tabs.map(formatTab) });
    });
    return true; // async response
  }
  if (message.type === "CLOSE_TAB") {
    chrome.tabs.remove(message.tabId);
    sendResponse({ ok: true });
    return true;
  }
  if (message.type === "FOCUS_TAB") {
    chrome.tabs.update(message.tabId, { active: true });
    chrome.windows.update(message.windowId, { focused: true });
    sendResponse({ ok: true });
    return true;
  }
});

function formatTab(tab) {
  return {
    id: tab.id,
    windowId: tab.windowId,
    url: tab.url,
    title: tab.title,
    favIconUrl: tab.favIconUrl || "",
    active: tab.active,
  };
}
""",
    "popup/popup.html": """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Tab Manager</title>
  <link rel="stylesheet" href="popup.css" />
</head>
<body>
  <div id="app">
    <header>
      <h1>Tab Manager</h1>
      <input id="search" type="text" placeholder="Search tabs..." />
    </header>
    <ul id="tab-list"></ul>
    <footer>
      <span id="tab-count">0 tabs</span>
      <button id="close-dupes">Close Duplicates</button>
    </footer>
  </div>
  <script src="popup.js"></script>
</body>
</html>
""",
    "popup/popup.css": """/* popup.css — Styles for the Tab Manager popup */

* {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

body {
  width: 360px;
  max-height: 500px;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  font-size: 13px;
  color: #1a1a2e;
  background: #f8f9fa;
}

header {
  padding: 12px;
  background: #4361ee;
  color: white;
}

header h1 {
  font-size: 16px;
  margin-bottom: 8px;
}

#search {
  width: 100%;
  padding: 6px 10px;
  border: none;
  border-radius: 6px;
  font-size: 13px;
}

#tab-list {
  list-style: none;
  max-height: 380px;
  overflow-y: auto;
}

.tab-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-bottom: 1px solid #e9ecef;
  cursor: pointer;
}

.tab-item:hover {
  background: #e7f0ff;
}

.tab-item img {
  width: 16px;
  height: 16px;
}

.tab-title {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tab-close {
  background: none;
  border: none;
  color: #adb5bd;
  cursor: pointer;
  font-size: 16px;
}

.tab-close:hover {
  color: #e63946;
}

footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  background: #f1f3f5;
  border-top: 1px solid #dee2e6;
}

#close-dupes {
  padding: 4px 10px;
  border: 1px solid #4361ee;
  border-radius: 4px;
  background: white;
  color: #4361ee;
  cursor: pointer;
  font-size: 12px;
}

#close-dupes:hover {
  background: #4361ee;
  color: white;
}
""",
    "popup/popup.js": """// popup.js — Logic for the Tab Manager popup UI

import { debounce } from "../utils/helpers.js";

const searchInput = document.getElementById("search");
const tabList = document.getElementById("tab-list");
const tabCount = document.getElementById("tab-count");
const closeDupesBtn = document.getElementById("close-dupes");

let allTabs = [];

// Fetch all open tabs from background
async function loadTabs() {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage({ type: "GET_TABS" }, (response) => {
      allTabs = response.tabs || [];
      resolve(allTabs);
    });
  });
}

function renderTabs(tabs) {
  tabList.innerHTML = "";
  tabs.forEach((tab) => {
    const li = document.createElement("li");
    li.className = "tab-item";
    li.innerHTML = `
      <img src="${tab.favIconUrl || "icons/default.png"}" alt="" />
      <span class="tab-title">${escapeHtml(tab.title)}</span>
      <button class="tab-close" data-tab-id="${tab.id}">&times;</button>
    `;
    li.addEventListener("click", () => focusTab(tab));
    li.querySelector(".tab-close").addEventListener("click", (e) => {
      e.stopPropagation();
      closeTab(tab.id);
    });
    tabList.appendChild(li);
  });
  tabCount.textContent = `${tabs.length} tab${tabs.length !== 1 ? "s" : ""}`;
}

function filterTabs(query) {
  const q = query.toLowerCase();
  return allTabs.filter(
    (t) =>
      t.title.toLowerCase().includes(q) || t.url.toLowerCase().includes(q)
  );
}

function focusTab(tab) {
  chrome.runtime.sendMessage({
    type: "FOCUS_TAB",
    tabId: tab.id,
    windowId: tab.windowId,
  });
  window.close();
}

function closeTab(tabId) {
  chrome.runtime.sendMessage({ type: "CLOSE_TAB", tabId }, () => {
    allTabs = allTabs.filter((t) => t.id !== tabId);
    renderTabs(filterTabs(searchInput.value));
  });
}

function findDuplicates(tabs) {
  const seen = new Map();
  const dupes = [];
  for (const tab of tabs) {
    if (seen.has(tab.url)) {
      dupes.push(tab.id);
    } else {
      seen.set(tab.url, tab.id);
    }
  }
  return dupes;
}

async function closeDuplicates() {
  const dupeIds = findDuplicates(allTabs);
  for (const id of dupeIds) {
    await new Promise((resolve) =>
      chrome.runtime.sendMessage({ type: "CLOSE_TAB", tabId: id }, resolve)
    );
  }
  await loadTabs();
  renderTabs(filterTabs(searchInput.value));
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// --- Event Listeners ---

searchInput.addEventListener(
  "input",
  debounce(() => {
    renderTabs(filterTabs(searchInput.value));
  }, 200)
);

closeDupesBtn.addEventListener("click", closeDuplicates);

// Init
loadTabs().then((tabs) => renderTabs(tabs));
""",
    "content/content.js": """// content.js — Content script injected into all pages

(function () {
  "use strict";

  // Highlight the active tab's page with a subtle border (for demo purposes)
  function addActiveBorder() {
    const border = document.createElement("div");
    border.id = "tab-manager-border";
    border.style.cssText =
      "position:fixed;top:0;left:0;right:0;height:3px;background:#4361ee;z-index:999999;pointer-events:none;";
    document.body.appendChild(border);

    setTimeout(() => {
      border.style.transition = "opacity 0.5s";
      border.style.opacity = "0";
      setTimeout(() => border.remove(), 500);
    }, 2000);
  }

  // Listen for messages from the popup/background
  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message.type === "HIGHLIGHT") {
      addActiveBorder();
      sendResponse({ ok: true });
    }
    if (message.type === "GET_PAGE_INFO") {
      sendResponse({
        title: document.title,
        url: window.location.href,
        description:
          document.querySelector('meta[name="description"]')?.content || "",
      });
    }
  });

  // Notify background that content script is loaded
  chrome.runtime.sendMessage({ type: "CONTENT_LOADED", url: window.location.href });
})();
""",
    "content/content.css": """/* content.css — Injected styles for Tab Manager content script */

#tab-manager-border {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: linear-gradient(90deg, #4361ee, #7209b7);
  z-index: 999999;
  pointer-events: none;
}
""",
    "utils/helpers.js": """// helpers.js — Shared utility functions

/**
 * Debounce a function call.
 * @param {Function} fn - The function to debounce.
 * @param {number} ms - Delay in milliseconds.
 * @returns {Function}
 */
export function debounce(fn, ms = 300) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

/**
 * Truncate a string to a maximum length with ellipsis.
 * @param {string} str
 * @param {number} maxLen
 * @returns {string}
 */
export function truncate(str, maxLen = 60) {
  if (str.length <= maxLen) return str;
  return str.slice(0, maxLen - 1) + "…";
}

/**
 * Format a timestamp as a human-readable relative time string.
 * @param {number} timestamp - Unix timestamp in milliseconds.
 * @returns {string}
 */
export function timeAgo(timestamp) {
  const seconds = Math.floor((Date.now() - timestamp) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}
""",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_test_project(test_dir: Path) -> None:
    """Write all sample Chrome extension files into test_dir."""
    if test_dir.exists():
        shutil.rmtree(test_dir)
    test_dir.mkdir(parents=True, exist_ok=True)

    for rel_path, content in EXTENSION_FILES.items():
        file_path = test_dir / rel_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Main test
# ---------------------------------------------------------------------------


async def main() -> None:
    test_dir = DEMO_CODE_BASE / "test_chrome_ext"
    _create_test_project(test_dir)
    current_project_dir.set(test_dir)

    print(f"Created test Chrome extension in {test_dir}")
    print(f"Files: {list(EXTENSION_FILES.keys())}\n")

    print("Building Graph RAG index...")
    index = await get_or_build_index(test_dir)
    print(
        f"  Graph: {index.graph.number_of_nodes()} nodes, "
        f"{index.graph.number_of_edges()} edges\n"
    )

    queries = [
        "How does the popup search and filter tabs?",
        "Where are duplicate tabs detected and closed?",
        "How does the background script track tab history?",
    ]

    for q in queries:
        print(f"Query: {q}")
        results = await index.search(q, top_k=3)
        if not results:
            print("  (no results)\n")
            continue
        for r in results:
            print(
                f"  [{r['score']:.4f}] {r['file']}:{r['start_line']}-{r['end_line']}"
            )
            if r.get("entities"):
                print(f"         entities: {', '.join(r['entities'])}")
            if r.get("related"):
                print(f"         related:  {', '.join(r['related'][:3])}")
        print()

    # Cleanup
    shutil.rmtree(test_dir)
    print("Cleaned up test project.")


if __name__ == "__main__":
    asyncio.run(main())
