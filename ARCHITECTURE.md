# Morph — Agent Architecture

> An AI-powered browser that builds its own Chrome extensions. The agent reasons, writes code, searches semantically, reads your tabs, remembers your preferences, and installs extensions — all in a multi-turn conversational loop.

---

## 1. Full System Architecture

```mermaid
flowchart TD
    classDef openai fill:#10a37f,color:#fff,stroke:#0d8c6d
    classDef browser fill:#4285f4,color:#fff,stroke:#3367d6
    classDef tool fill:#ff6d00,color:#fff,stroke:#e65100
    classDef memory fill:#ab47bc,color:#fff,stroke:#8e24aa
    classDef graphrag fill:#00bcd4,color:#fff,stroke:#0097a7
    classDef storage fill:#78909c,color:#fff,stroke:#546e7a
    classDef agent fill:#e91e63,color:#fff,stroke:#c2185b

    %% ─── BROWSER LAYER ───────────────────────────────────────────────
    subgraph CHROME ["🌐 Chrome Browser"]
        direction LR
        SP["Sidepanel Chat UI<br/><i>React + TypeScript</i>"]:::browser
        CS["Content Scripts<br/><i>DOM extraction · console capture</i>"]:::browser
        TABS["Open Browser Tabs<br/><i>tab metadata → agent context</i>"]:::browser
    end

    %% ─── COMMUNICATION ──────────────────────────────────────────────
    SP <-->|"WebSocket<br/>bidirectional JSON"| WS_EP
    CS <-->|"chrome.tabs.sendMessage"| SP

    %% ─── BACKEND ─────────────────────────────────────────────────────
    subgraph BACKEND ["⚙️ FastAPI Backend"]
        direction TB

        WS_EP["/ws/{project_id}<br/><i>WebSocket endpoint</i>"]
        REST["REST API<br/><i>projects · conversations · rules</i>"]

        %% ─── AGENT CORE ─────────────────────────────────────────────
        subgraph AGENT_CORE ["🤖 CodingAgent — Agentic Loop"]
            direction TB
            GPT5["<b>GPT-5.2</b><br/>Primary Agent<br/><i>reasoning · planning<br/>tool orchestration</i>"]:::openai
            PROMPT["System Prompt Builder<br/><i>+ active tabs + memory rules<br/>+ tool availability</i>"]:::agent
            STREAM["Streaming Tool Loop<br/><i>plan → act → observe → repeat</i>"]:::agent

            GPT5 --> PROMPT --> STREAM
        end

        %% ─── SUPPORTING MODELS ───────────────────────────────────────
        subgraph MODELS ["🧠 Supporting OpenAI Models"]
            GPT5NANO["<b>GPT-5 Nano</b><br/>Secondary<br/><i>code edits · titles<br/>rules · entities</i>"]:::openai
            EMB_OAI["<b>text-embedding-3-small</b><br/>Embeddings"]:::openai
        end

        %% ─── TOOL SYSTEM ────────────────────────────────────────────
        subgraph TOOLS ["🔧 Tool System — 11 Tools"]
            direction TB
            subgraph FS_TOOLS ["Filesystem"]
                LIST["list_dir"]:::tool
                READ["read_file"]:::tool
                GREP["grep_search<br/><i>ripgrep</i>"]:::tool
                CREATE["create_file"]:::tool
                EDIT["edit_file<br/><i>+ secondary LLM apply</i>"]:::tool
            end
            subgraph BROWSER_TOOLS ["Browser Integration"]
                GET_TAB["get_tab_content<br/><i>paginated · cached</i>"]:::tool
                GET_LOGS["get_console_logs<br/><i>filterable · paginated</i>"]:::tool
            end
            subgraph EXT_TOOLS ["Extension Lifecycle"]
                VALIDATE["validate_extension<br/><i>manifest · files · syntax<br/>MV3 compat</i>"]:::tool
                LOAD["load_extension<br/><i>→ install card in UI</i>"]:::tool
            end
            TERM["run_terminal_command<br/><i>sandboxed · bg support</i>"]:::tool
            CSEARCH["codebase_search<br/><i>Graph RAG semantic</i>"]:::tool
        end

        STREAM --> TOOLS

        %% ─── GRAPH RAG ──────────────────────────────────────────────
        subgraph GRAPH_RAG ["🕸️ Graph RAG Engine"]
            direction TB
            subgraph INDEX_PIPE ["Indexing Pipeline"]
                direction LR
                WALK["Walk<br/>Files"] --> CHUNK["Chunk<br/>60-line segments"]
                CHUNK --> ENT_EX["LLM Entity<br/>Extraction"]
                CHUNK --> EMB_CH["Embed<br/>Chunks"]
                ENT_EX --> BUILD_G["Build<br/>NetworkX Graph"]
                EMB_CH --> VEC_STORE["Vector<br/>Store"]
            end
            subgraph SEARCH_PIPE ["Search Pipeline"]
                direction LR
                EMB_Q["Embed<br/>Query"] --> COS["Cosine<br/>Similarity"]
                COS --> TOP_K["Top-K<br/>Vector Hits"]
                TOP_K --> GRAPH_EXP["Graph<br/>Traversal<br/>1-2 hops"]
                GRAPH_EXP --> RERANK["Re-rank<br/><i>0.75·vec + 0.25·graph</i>"]
            end
            KG[("Knowledge Graph<br/><i>files · functions · classes<br/>IMPORTS · CALLS · EXPORTS<br/>EXTENDS · USES</i>")]:::graphrag
        end

        CSEARCH --> SEARCH_PIPE
        BUILD_G --> KG
        GRAPH_EXP --> KG
        ENT_EX -.->|"secondary LLM"| GPT5NANO
        EMB_CH -.-> EMB_OAI
        EMB_Q -.-> EMB_OAI

        %% ─── MEMORY SYSTEM ──────────────────────────────────────────
        subgraph MEMORY ["🧠 Agent Memory System"]
            direction LR
            RULE_EX["Rule Extractor<br/><i>post-turn background task</i>"]:::memory
            RULE_DB["Per-Project Rules<br/><i>preferences · corrections<br/>style · constraints</i>"]:::memory
            RULE_INJ["Rule Injection<br/><i>→ system prompt</i>"]:::memory
            RULE_EX --> RULE_DB --> RULE_INJ
        end

        RULE_EX -.->|"secondary LLM"| GPT5NANO
        RULE_INJ --> PROMPT

        %% ─── STORAGE ────────────────────────────────────────────────
        DB[("SQLite<br/><i>projects · conversations<br/>messages · rules</i>")]:::storage
        FS[("demo_code/{project}/<br/><i>generated extension files</i>")]:::storage
    end

    %% ─── CROSS-LAYER CONNECTIONS ────────────────────────────────────
    WS_EP --> AGENT_CORE
    REST --> DB
    WS_EP <--> DB

    GET_TAB -->|"request_tab_content<br/>via outbound queue"| WS_EP
    GET_LOGS -->|"request_console_logs<br/>via outbound queue"| WS_EP
    LOAD -->|"extension_ready<br/>via outbound queue"| WS_EP

    EDIT -.->|"secondary LLM"| GPT5NANO

    FS_TOOLS --> FS
    TERM --> FS
    INDEX_PIPE -.-> FS

    TABS --> SP
    WS_EP -->|"request_tab_content"| SP
    SP -->|"tab_content_response"| WS_EP
```

---

## 2. Multi-Turn Conversational Agent Flow

> *Targeting: Decagon / Greylock / Google — Best Multi-Turn Conversational Agent*

```mermaid
sequenceDiagram
    autonumber
    participant User as 👤 User
    participant SP as 📱 Sidepanel
    participant WS as 🔌 WebSocket
    participant Agent as 🤖 CodingAgent
    participant Tools as 🔧 Tools
    participant Memory as 🧠 Memory
    participant DB as 💾 SQLite

    rect rgb(66, 133, 244, 0.1)
        Note over User,DB: Turn 1 — User describes what they want
        User->>SP: "Build me a tab grouper extension"
        SP->>SP: Query chrome.tabs for open tabs
        SP->>WS: {type: "chat", query, active_tabs: [...], provider: "openai"}
        WS->>DB: save_message(user)
        WS->>DB: get_rules(project_id)
        WS->>Agent: stream_chat_response(history, tabs, rules)

        Agent->>Agent: Build system prompt<br/>+ inject active tabs<br/>+ inject memory rules
        
        loop Agentic Tool Loop (plan → act → observe → repeat)
            Agent->>WS: {type: "content", "I'll start by creating..."}
            WS->>SP: stream text chunks
            Agent->>WS: {type: "tool_start", name: "create_file"}
            Agent->>Tools: create_file("manifest.json", ...)
            Tools-->>Agent: "Successfully created manifest.json"
            Agent->>WS: {type: "tool_end", name: "create_file"}

            Agent->>WS: {type: "tool_start", name: "create_file"}
            Agent->>Tools: create_file("content.js", ...)
            Tools-->>Agent: "Successfully created content.js"
            Agent->>WS: {type: "tool_end", name: "create_file"}
            
            Agent->>WS: {type: "tool_start", name: "get_tab_content"}
            Note over Agent,Tools: Tool pushes outbound event & awaits Future
            Tools->>WS: {type: "request_tab_content", tab_id, request_id}
            WS->>SP: forward request
            SP->>SP: chrome.tabs.sendMessage → content script
            SP->>WS: {type: "tab_content_response", content: "..."}
            WS->>Agent: resolve Future with page content
            Agent->>WS: {type: "tool_end", name: "get_tab_content"}

            Agent->>WS: {type: "tool_start", name: "validate_extension"}
            Agent->>Tools: validate_extension()
            Tools-->>Agent: "Validation passed"
            Agent->>WS: {type: "tool_end"}

            Agent->>WS: {type: "tool_start", name: "load_extension"}
            Agent->>Tools: load_extension()
            Tools->>WS: {type: "extension_ready", path: "/..."}
            WS->>SP: render install card
            Agent->>WS: {type: "tool_end"}
        end

        Agent->>WS: {type: "content", "Your extension is ready!"}
        WS->>DB: save_message(assistant)
        WS->>SP: {type: "done", conversation_id, content}

        par Background Tasks
            WS->>Memory: extract_rules(history, existing_rules)
            Memory->>DB: save_rules(new_rules)
            Memory->>WS: {type: "rules_updated"}
            WS->>SP: update memory UI
        and
            WS->>Agent: generate_conversation_title()
            Agent->>WS: {type: "conversation_title", title}
            WS->>SP: update sidebar
        end
    end

    rect rgb(171, 71, 188, 0.1)
        Note over User,DB: Turn 2 — User requests a change (memory-informed)
        User->>SP: "Make it group by domain instead"
        SP->>WS: {type: "chat", query, active_tabs}
        WS->>DB: get_rules → ["User prefers minimal UI", ...]
        WS->>Agent: stream(history, rules=["User prefers minimal UI", ...])
        
        Agent->>Agent: System prompt now includes<br/>learned rules from Turn 1

        loop Agentic Tool Loop
            Agent->>WS: {type: "tool_start", name: "codebase_search"}
            Agent->>Tools: codebase_search("tab grouping logic")
            Note over Tools: Graph RAG: embed query → cosine sim → graph traversal → re-rank
            Tools-->>Agent: Top 5 results with relationship context
            Agent->>WS: {type: "tool_end"}

            Agent->>WS: {type: "tool_start", name: "edit_file"}
            Agent->>Tools: edit_file("content.js", "group by domain", code_edit)
            Note over Tools: Secondary LLM merges edit into original file
            Tools-->>Agent: "Successfully edited content.js"
            Agent->>WS: {type: "tool_end"}

            Agent->>WS: {type: "tool_start", name: "get_console_logs"}
            Note over Agent,Tools: Fetch runtime logs to verify no errors
            Tools->>WS: {type: "request_console_logs", tab_id}
            WS->>SP: forward request
            SP->>WS: {type: "console_logs_response", content}
            Agent->>WS: {type: "tool_end"}
        end

        Agent->>WS: {type: "content", "Done! The extension now groups by domain."}
        WS->>DB: save_message(assistant)
        
        par Background
            Memory->>DB: extract + save new rules
        end
    end
```

---

## 3. Graph RAG — Knowledge Graph Search Engine

> *Semantic code search that understands code structure, not just text matching*

```mermaid
flowchart TB
    classDef llm fill:#10a37f,color:#fff
    classDef embed fill:#00bcd4,color:#fff
    classDef graph fill:#ff6d00,color:#fff
    classDef data fill:#78909c,color:#fff

    subgraph INDEXING ["📦 Indexing Pipeline (triggered on first search per project)"]
        direction TB

        FILES["📁 Walk Project Files<br/><i>skip: node_modules, .git, binaries<br/>index: .js .ts .py .html .css .json ...</i>"]:::data

        FILES --> CHUNK["✂️ Chunk into 60-line Segments<br/><i>10-line overlap between chunks</i>"]

        CHUNK --> LLM_EX["🤖 LLM Entity Extraction<br/><i>one call per file</i><br/><br/>Extract:<br/>• functions, classes, components<br/>• imports, calls, exports, extends"]:::llm
        
        CHUNK --> EMBED["🔢 Embed Chunks<br/><i>batch of 100</i>"]:::embed

        LLM_EX --> GRAPH_BUILD["🕸️ Build Knowledge Graph<br/><i>NetworkX DiGraph</i>"]:::graphrag

        GRAPH_BUILD --> NODES["<b>Nodes:</b><br/>• file:path<br/>• chunk:path:lines<br/>• entity:path:name<br/>• ref:module"]
        GRAPH_BUILD --> EDGES["<b>Edges:</b><br/>• CONTAINS (file → chunk)<br/>• DEFINES (file → entity)<br/>• IMPORTS (file → file/module)<br/>• CALLS (func → func)<br/>• EXPORTS (file → symbol)<br/>• EXTENDS (class → class)<br/>• USES (func → variable)"]

        EMBED --> MATRIX["📊 Embedding Matrix<br/><i>n_chunks × 1536 dims</i>"]:::data
    end

    subgraph SEARCH ["🔍 Search Pipeline (per query)"]
        direction TB

        QUERY["❓ Natural Language Query<br/><i>'handle user click on tab group'</i>"]

        QUERY --> EMB_Q["🔢 Embed Query"]:::embed
        EMB_Q --> COSINE["📐 Cosine Similarity<br/><i>query vec × embedding matrix</i>"]
        COSINE --> TOP10["🏆 Top 10 Vector Hits"]

        TOP10 --> GRAPH_WALK["🚶 Graph Traversal<br/><i>BFS 1-2 hops from each hit's file node<br/>undirected view</i>"]:::graphrag

        GRAPH_WALK --> BONUS["📈 Graph Proximity Bonus<br/><i>bonus = hit_score × 1/(1+distance)</i>"]

        BONUS --> COMBINE["⚖️ Combined Score<br/><i>final = vec_score + 0.25 × graph_bonus</i>"]

        COMBINE --> RESULTS["📋 Top 5 Results<br/><i>file:lines + code + entities + relationships</i>"]
    end

    MATRIX --> COSINE
    NODES --> GRAPH_WALK
    EDGES --> GRAPH_WALK

    subgraph CACHE ["♻️ Intelligent Caching"]
        FP["File fingerprint (MD5 of paths + mtimes)"]
        HIT["Cache hit → skip rebuild"]
        MISS["Cache miss → background rebuild"]
    end
```

---

## 4. OpenAI Model Architecture

> *Targeting: OpenAI — Best Use of OpenAI API*

```mermaid
flowchart LR
    classDef openai fill:#10a37f,color:#fff,stroke:#0d8c6d
    subgraph OPENAI_STACK ["OpenAI Stack"]
        direction TB
        GPT5["<b>GPT-5.2</b><br/><i>Primary Agent LLM</i><br/>reasoning · planning<br/>11-tool orchestration<br/>multi-step coding"]:::openai
        
        GPT5NANO["<b>GPT-5 Nano</b><br/><i>Secondary LLM (4 use cases)</i>"]:::openai
        
        EMB_O["<b>text-embedding-3-small</b><br/><i>Graph RAG Embeddings</i>"]:::openai

        GPT5NANO --- U1["① edit_file — code merge"]
        GPT5NANO --- U2["② Title generation"]
        GPT5NANO --- U3["③ Rule extraction (memory)"]
        GPT5NANO --- U4["④ Entity extraction (Graph RAG)"]
    end

    subgraph API_COMPAT ["🔌 API Compatibility Layer"]
        LC["LangChain<br/>init_chat_model()<br/>bind_tools()"]
        AOAI["AsyncOpenAI<br/>OpenAI-compatible<br/>endpoint"]
    end

    GPT5 --> LC
    GPT5NANO --> AOAI
    EMB_O --> AOAI
```

---

## 5. Agent Memory — Learning Across Conversations

> *Targeting: Best Multi-Turn Conversational Agent*

```mermaid
flowchart TB
    classDef memory fill:#ab47bc,color:#fff
    classDef llm fill:#10a37f,color:#fff
    classDef db fill:#78909c,color:#fff

    subgraph TURN_N ["Conversation Turn N"]
        USER_MSG["👤 User: 'Always use TypeScript,<br/>and keep the UI minimal'"]
        AGENT_RESP["🤖 Agent: 'Sure, I'll use TypeScript...'"]
    end

    subgraph EXTRACTION ["Background Rule Extraction (async)"]
        direction TB
        FULL_HIST["Full Conversation<br/>History"]
        EXISTING["Existing Rules<br/><i>from previous turns</i>"]
        
        PROMPT["Extraction Prompt<br/><i>'Extract new behavioral rules...<br/>don't duplicate existing ones'</i>"]:::memory

        LLM_CALL["Secondary LLM Call<br/><i>GPT-4o-mini / Nemotron Nano</i>"]:::llm

        NEW_RULES["New Rules:<br/>• 'Always use TypeScript for extension code'<br/>• 'Keep UI minimal and clean'"]:::memory
    end

    FULL_HIST --> PROMPT
    EXISTING --> PROMPT
    PROMPT --> LLM_CALL --> NEW_RULES

    subgraph STORAGE ["Persistence"]
        DB[("SQLite<br/>rules table<br/><i>id · project_id · content · created_at</i>")]:::db
    end

    NEW_RULES --> DB

    subgraph TURN_N_PLUS_1 ["Conversation Turn N+1"]
        direction TB
        LOAD_RULES["Load Rules from DB"]
        SYS_PROMPT["System Prompt Injection:<br/><br/><b>## Agent Memory</b><br/>- Always use TypeScript for extension code<br/>- Keep UI minimal and clean<br/>- User prefers tabs over spaces<br/>- ..."]:::memory
        
        AGENT_N1["🤖 Agent now follows<br/>all learned rules<br/>without being told again"]
    end

    DB --> LOAD_RULES --> SYS_PROMPT --> AGENT_N1

    subgraph MANAGEMENT ["User Control"]
        VIEW["📋 View all rules in Memory panel"]
        DELETE["🗑️ Delete incorrect rules"]
        WS_UPDATE["Real-time updates via WebSocket"]
    end

    TURN_N --> EXTRACTION
    DB --> MANAGEMENT
```

---

## 6. Browser Integration — Bidirectional WebSocket

```mermaid
flowchart LR
    classDef fe fill:#4285f4,color:#fff
    classDef ws fill:#ff6d00,color:#fff
    classDef be fill:#e91e63,color:#fff

    subgraph FRONTEND ["Chrome Extension"]
        direction TB
        PANEL["Sidepanel<br/><i>React Chat UI</i>"]:::fe
        
        subgraph SEND ["Messages → Backend"]
            S1["chat: query + active_tabs"]
            S2["tab_content_response: page text/HTML"]
            S3["console_logs_response: log entries"]
        end

        subgraph RECEIVE ["Messages ← Backend"]
            R1["content: streamed text chunks"]
            R2["tool_start / tool_end: tool status"]
            R3["request_tab_content: fetch page"]
            R4["request_console_logs: fetch logs"]
            R5["extension_ready: install card"]
            R6["conversation_title: auto-generated"]
            R7["rules_updated: new memory rules"]
            R8["thinking: agent processing"]
            R9["done: turn complete"]
        end

        CS2["Content Scripts<br/><i>injected into all http(s) pages</i>"]:::fe
    end

    subgraph WEBSOCKET ["WebSocket /ws/{project_id}"]
        direction TB
        LISTENER["Async Listener Task<br/><i>routes incoming messages</i>"]:::ws
        OUTBOUND["Outbound Queue<br/><i>tool → WS → browser</i>"]:::ws
        PENDING["Pending Requests<br/><i>asyncio.Future per request</i>"]:::ws
    end

    subgraph BACKEND_CORE ["Agent Core"]
        AGENT2["CodingAgent<br/>Streaming Loop"]:::be
        TOOL_SYS["Tool System<br/><i>contextvars for async state</i>"]:::be
    end

    PANEL --> S1 --> LISTENER
    LISTENER --> AGENT2
    
    AGENT2 --> R1 --> PANEL
    AGENT2 --> R2 --> PANEL

    TOOL_SYS -->|"push event"| OUTBOUND
    OUTBOUND --> R3 --> PANEL
    PANEL --> CS2
    CS2 --> PANEL
    PANEL --> S2 --> LISTENER
    LISTENER -->|"resolve Future"| PENDING
    PENDING --> TOOL_SYS
```

---

## 7. Extension Development Lifecycle

```mermaid
flowchart TD
    classDef agent fill:#e91e63,color:#fff
    classDef tool fill:#ff6d00,color:#fff
    classDef check fill:#4caf50,color:#fff
    classDef user fill:#4285f4,color:#fff

    START["👤 User: 'Build me a dark mode toggler extension'"]:::user

    START --> PLAN["🤖 Agent plans the extension<br/><i>manifest.json · content scripts<br/>popup · service worker</i>"]:::agent

    PLAN --> TAB_READ["🔧 get_tab_content<br/><i>Read user's active tab to understand<br/>the target site's DOM structure</i>"]:::tool

    TAB_READ --> CODE_GEN

    subgraph CODE_GEN ["Code Generation Loop"]
        direction TB
        CF1["🔧 create_file manifest.json"]:::tool
        CF2["🔧 create_file content.js"]:::tool
        CF3["🔧 create_file popup.html"]:::tool
        CF4["🔧 create_file styles.css"]:::tool
        CF1 --> CF2 --> CF3 --> CF4
    end

    CODE_GEN --> VALIDATE_1["🔧 validate_extension<br/><i>4-layer validation:<br/>① manifest fields<br/>② file references<br/>③ JS syntax (node --check)<br/>④ MV3 compatibility</i>"]:::check

    VALIDATE_1 -->|"errors found"| FIX

    subgraph FIX ["Iterative Fix Loop"]
        direction TB
        SEARCH["🔧 codebase_search / grep_search<br/><i>find the problematic code</i>"]:::tool
        EDIT_FIX["🔧 edit_file<br/><i>secondary LLM applies fix</i>"]:::tool
        READ_CHK["🔧 read_file<br/><i>verify the fix</i>"]:::tool
        SEARCH --> EDIT_FIX --> READ_CHK
    end

    FIX --> VALIDATE_2["🔧 validate_extension"]:::check
    VALIDATE_1 -->|"passed ✓"| INSTALL
    VALIDATE_2 -->|"passed ✓"| INSTALL

    INSTALL["🔧 load_extension<br/><i>sends extension_ready event<br/>via WebSocket to sidepanel</i>"]:::tool

    INSTALL --> CARD["📱 Install Card in Chat<br/><i>• Load Extension button (auto)<br/>• Copy Path (manual fallback)<br/>• Open chrome://extensions</i>"]:::user

    CARD --> TEST["👤 User tests extension"]:::user
    TEST -->|"'It's not working on YouTube'"| DEBUG

    subgraph DEBUG ["Debug Loop"]
        direction TB
        LOGS["🔧 get_console_logs<br/><i>fetch runtime errors</i>"]:::tool
        TAB2["🔧 get_tab_content<br/><i>inspect current DOM state</i>"]:::tool
        CS2["🔧 codebase_search<br/><i>find related code via Graph RAG</i>"]:::tool
        EDIT2["🔧 edit_file<br/><i>apply the fix</i>"]:::tool
        LOGS --> TAB2 --> CS2 --> EDIT2
    end

    DEBUG --> VALIDATE_3["🔧 validate_extension"]:::check
    VALIDATE_3 -->|"passed ✓"| RELOAD["🔧 load_extension<br/><i>user reloads extension</i>"]:::tool
    RELOAD --> DONE["✅ Extension working!"]:::check
```

---

## Key Technical Highlights

| Feature | Implementation | Prize Relevance |
|---------|---------------|-----------------|
| **Multi-model orchestration** | GPT-5.2 primary + GPT-5 Nano secondary (4 use cases) + embeddings | OpenAI |
| **Graph RAG search** | NetworkX knowledge graph + vector embeddings + BFS traversal | All |
| **Agent memory** | Background rule extraction → SQLite → system prompt injection | Conversational |
| **11-tool agentic loop** | Plan → Act → Observe → Repeat with streaming | All |
| **Bidirectional WebSocket** | Agent can request data FROM the browser mid-turn | Conversational |
| **Tab awareness** | Active tabs in system prompt, on-demand DOM/HTML fetching | Conversational |
| **Console log access** | Runtime debugging without leaving the chat | Conversational |
| **Extension lifecycle** | Code → Validate (4 layers) → Install → Debug → Iterate | All |
| **Streaming UX** | Real-time tool status, text streaming, async background tasks | All |
