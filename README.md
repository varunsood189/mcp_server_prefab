# Assignment 4: MCP Server with Internet + CRUD + Prefab UI

A working **Model Context Protocol (MCP) server** built with [FastMCP](https://github.com/jlowin/fastmcp), exposing three tools — internet fetch, local file CRUD, and a Prefab UI dashboard — to any MCP-compatible client (Claude, Cursor, or a custom script).

---

## What is MCP?

**MCP (Model Context Protocol)** is an open standard by Anthropic that lets AI models talk to external tools, APIs, files, and UIs in a structured way. Instead of each AI app building its own custom integrations, MCP provides a universal protocol:

- The **server** exposes tools (functions the AI can call)
- The **client** (an LLM or script) discovers and calls those tools
- Communication happens over stdio or HTTP using JSON-RPC

This project is a minimal MCP server that demonstrates all three required capability types.

---

## Project Structure

```
assignment_mcp_prefab/
├── server.py          # MCP server — defines all 3 tools
├── client_demo.py     # Scripted MCP client (direct + Gemini planner mode)
├── streamlit_app.py   # Optional Streamlit input UI for running the MCP flow
├── requirements.txt   # Python dependencies
├── .env               # Optional local secrets (GEMINI_API_KEY), gitignored
└── data/              # Sandboxed folder for local file operations
    └── README.md      # Notes on CRUD scope
```

---

## The Three Tools

### 1. `fetch_company_ownership(company_name)`
Queries the Wikipedia REST summary and MediaWiki Parse APIs. Extracts ownership hints by regex-matching `owner` and `parent` lines in the company's infobox.

**Returns:** `company`, `source_url`, `fetched_at`, `summary`, `ownership_hints`, `report_text`

### 2. `local_file_crud(operation, filename, content="")`
Create, read, update, or delete files inside the `data/` folder. Path traversal is blocked — filenames must resolve within `data/`.

| operation | behaviour |
|-----------|-----------|
| `create`  | Write new file; error if already exists |
| `read`    | Return file content |
| `update`  | Overwrite existing file |
| `delete`  | Remove file |

### 3. `ownership_dashboard(filename, heading="Ownership Dashboard")`
Decorated with `@mcp.tool(app=True)`. Reads the saved file and returns a **PrefabApp** Card for the FastMCP browser preview. This tool is read-only — it does not write files.

**Returns:** A rendered Prefab card showing file status and up to 5,000 characters of content.

---

## Setup

> Requires Python 3.10+

```bash
cd "assignment_mcp_prefab"
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Running the Project

## Quick Clarification (Your Understanding)

- `fastmcp dev apps server.py` = **interactive tool testing UI** (browser)
- `python client_demo.py` = **scripted MCP client run** (terminal)
- Real "AI agent" = an LLM client (Cursor/Claude/etc.) connected to this MCP server

Your understanding is mostly right: use `dev apps` to test tools/UI, and `client_demo.py` to run the flow automatically.

---

### Option A — Browser UI with Prefab preview (recommended)

Launches the FastMCP dev server and opens a browser where you can call tools interactively and see the Prefab dashboard rendered visually.

```bash
source .venv/bin/activate
fastmcp dev apps server.py --mcp-port 8001 --ui-port 8002
```

Open the printed URL in your browser, then paste the forced prompt below into the tool runner.

### Option B — Scripted Python client (`client_demo.py`)

Runs all 4 tool calls automatically via stdio transport and prints results in terminal. Does **not** render Prefab visually.

> Important: `client_demo.py` is a script (MCP client), not an LLM by itself. It simulates tool-calling flow.

```bash
source .venv/bin/activate
python client_demo.py --company "Tata Sons"
```

With custom arguments:

```bash
python client_demo.py \
  --company "Tata Sons" \
  --filename "tata_sons_ownership.txt" \
  --heading "Tata Sons Ownership Dashboard"
```

### Option C — Scripted client with Gemini tool planning

Uses **Gemini `gemini-2.5-flash-lite`** to decide each tool call step-by-step, while this project still executes the MCP tools (`fetch_company_ownership`, `local_file_crud`, `ownership_dashboard`) in sequence.

```bash
source .venv/bin/activate
python client_demo.py \
  --use-llm \
  --company "Tata Sons" \
  --filename "tata_sons_ownership.txt" \
  --heading "Tata Sons Ownership Dashboard"
```

Notes:
- `client_demo.py` auto-loads `.env` and reads `GEMINI_API_KEY` if present.
- `--gemini-api-key "..."` can be passed directly and takes precedence.
- This mode prints an LLM reasoning trace and tool call payloads in terminal.
- Visual Prefab rendering still requires `fastmcp dev apps server.py`.

### Option D — Streamlit input UI

Use Streamlit as a second UI to collect user input and run the same MCP flow.

```bash
source .venv/bin/activate
streamlit run streamlit_app.py
```

Notes:
- Supports direct mode and Gemini planner mode from the UI.
- Prefab dashboard tool is still called in the flow; visual Prefab rendering remains via `fastmcp dev apps`.

---

## Required Forced Prompt

Use this prompt in the FastMCP browser UI (Option A) to exercise all 3 tools in one flow:

```
You must call all 3 tools in this exact order and show intermediate outputs:
1) Call fetch_company_ownership with company_name="Tata Sons".
2) Convert the response into a concise report and call local_file_crud with operation="create", filename="tata_sons_ownership.txt", and that report as content.
3) Call local_file_crud with operation="read", filename="tata_sons_ownership.txt" to verify saved content.
4) Call ownership_dashboard with filename="tata_sons_ownership.txt" and heading="Tata Sons Ownership Dashboard".
Do not skip any step and do not answer only in plain text; return the Prefab dashboard at the end.
```

> If `create` fails with "already exists", `client_demo.py` automatically retries with `update`.

---

## Architecture

```
Entry Points
├── fastmcp dev apps server.py   (browser, MCP protocol)
├── python client_demo.py        (stdio transport)
└── Any LLM MCP client           (MCP protocol)
         |
         v
    server.py  (FastMCP server)
    ├── fetch_company_ownership  -->  Wikipedia REST + MediaWiki APIs
    ├── local_file_crud          -->  data/ folder (sandboxed)
    └── ownership_dashboard      -->  data/ folder (read)
                                 -->  Prefab browser preview
```

---

## Validation Checklist

- **Internet capability**
  - [ ] Call `fetch_company_ownership("Tata Sons")`
  - [ ] Result includes `summary` and `ownership_hints`

- **CRUD capability**
  - [ ] `create` a file in `data/`
  - [ ] `read` it back and verify content
  - [ ] `update` it with changed text
  - [ ] `delete` it

- **Prefab UI capability**
  - [ ] Call `ownership_dashboard("tata_sons_ownership.txt", ...)`
  - [ ] A rendered card appears in the browser preview
  - [ ] If file does not exist, card shows `Exists: No`

- **End-to-end**
  - [ ] Run the forced prompt and confirm all 3 capabilities are exercised in one flow

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `fastmcp` | MCP server framework — `@mcp.tool`, `Client`, `mcp.run()`, dev server |
| `prefab-ui` | Prefab component library — `PrefabApp`, `Card`, `Text` for the dashboard tool |
| `streamlit` | Optional web UI for collecting user input and running the flow |

Standard library only for HTTP (`urllib.request`), parsing (`re`), and file I/O (`pathlib`) — no extra HTTP client needed.

---

## Notes

- All file operations are sandboxed under `data/`. The `_safe_file_path` helper rejects any filename that resolves outside this directory.
- `ownership_dashboard` is intentionally read-only — use `local_file_crud` to create or update files before calling the dashboard.
- For visual Prefab rendering, always use `fastmcp dev apps server.py`. The scripted client proves tool calls and data flow but does not render Prefab visually.
