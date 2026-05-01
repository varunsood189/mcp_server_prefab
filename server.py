from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen

from fastmcp import FastMCP
from prefab_ui.app import PrefabApp
from prefab_ui.actions import SetState, ShowToast
from prefab_ui.actions.mcp import CallTool
from prefab_ui.components import (
    Button,
    Card,
    CardContent,
    CardHeader,
    CardTitle,
    Column,
    ERROR,
    H3,
    If,
    Input,
    Markdown,
    Muted,
    RESULT,
    Row,
    Separator,
    STATE,
    Text,
)


mcp = FastMCP("Assignment4MCPPrefabServer")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _safe_file_path(filename: str) -> Path:
    """Resolve filename under data folder and block path traversal."""
    candidate = (DATA_DIR / filename).resolve()
    if DATA_DIR not in candidate.parents and candidate != DATA_DIR:
        raise ValueError("Invalid filename: path traversal is not allowed.")
    return candidate


def _default_ownership_filename(company: str) -> str:
    """Same rules as ``client_demo.py``: ``<sanitized_lowercase>_ownership.txt``."""
    safe = re.sub(r"[^\w\s.-]", "", company.strip(), flags=re.UNICODE)
    safe = re.sub(r"\s+", "_", safe).strip("_").lower() or "report"
    return f"{safe}_ownership.txt"


def _http_get_json(url: str) -> dict[str, Any]:
    req = Request(
        url=url,
        headers={
            "User-Agent": "assignment-mcp-prefab/1.0 (educational project)",
            "Accept": "application/json",
        },
    )
    with urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _build_ownership_report(payload: dict[str, Any]) -> str:
    company = payload.get("company", "Unknown Company")
    source_page = payload.get("source_page", "N/A")
    fetched_at = payload.get("fetched_at_utc", "N/A")
    summary = payload.get("summary", "")
    hints = payload.get("ownership_hints", [])
    hint_block = "\n".join(f"- {item}" for item in hints) if hints else "- No explicit owner/parent line found"
    return (
        f"Company: {company}\n"
        f"Source: {source_page}\n"
        f"Fetched At (UTC): {fetched_at}\n\n"
        "Summary:\n"
        f"{summary}\n\n"
        "Ownership Hints:\n"
        f"{hint_block}\n"
    )


@mcp.tool()
def fetch_company_ownership(company_name: str) -> dict[str, Any]:
    """
    Internet tool:
    Fetch company ownership hints from Wikipedia summary + infobox wikitext.
    """
    page_title = company_name.strip().replace(" ", "_")
    if not page_title:
        return {"ok": False, "error": "company_name cannot be empty"}

    summary_url = (
        "https://en.wikipedia.org/api/rest_v1/page/summary/" + quote(page_title, safe="_")
    )
    parse_url = (
        "https://en.wikipedia.org/w/api.php"
        f"?action=parse&page={quote(page_title, safe='_')}&prop=wikitext&format=json"
    )

    try:
        summary = _http_get_json(summary_url)
        parse_json = _http_get_json(parse_url)
    except Exception as exc:
        return {
            "ok": False,
            "company": company_name.strip(),
            "error": f"Failed to fetch company data: {exc}",
        }

    extract = summary.get("extract", "")
    wikitext = parse_json.get("parse", {}).get("wikitext", {}).get("*", "")
    owner_matches = re.findall(r"^\|\s*owner\s*=\s*(.+)$", wikitext, flags=re.MULTILINE)
    parent_matches = re.findall(r"^\|\s*parent\s*=\s*(.+)$", wikitext, flags=re.MULTILINE)

    ownership_lines: list[str] = []
    for raw in owner_matches + parent_matches:
        cleaned = re.sub(r"<.*?>", "", raw)
        cleaned = re.sub(r"\[\[|\]\]", "", cleaned).strip()
        if cleaned:
            ownership_lines.append(cleaned)

    result: dict[str, Any] = {
        "ok": True,
        "company": company_name.strip(),
        "source_page": summary.get("content_urls", {}).get("desktop", {}).get("page"),
        "summary": extract,
        "ownership_hints": ownership_lines,
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    result["report_text"] = _build_ownership_report(result)
    return result


@mcp.tool()
def local_file_crud(operation: str, filename: str, content: str = "") -> dict[str, Any]:
    """
    Local file CRUD tool:
    operation in {create, read, update, delete}
    """
    op = operation.lower().strip()
    try:
        path = _safe_file_path(filename.strip())
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    if op == "create":
        if path.exists():
            return {"ok": False, "error": f"File already exists: {path.name}"}
        path.write_text(content, encoding="utf-8")
        return {"ok": True, "operation": op, "file": path.name, "bytes": len(content.encode("utf-8"))}

    if op == "read":
        if not path.exists():
            return {"ok": False, "error": f"File not found: {path.name}"}
        text = path.read_text(encoding="utf-8")
        return {"ok": True, "operation": op, "file": path.name, "content": text}

    if op == "update":
        if not path.exists():
            return {"ok": False, "error": f"File not found: {path.name}"}
        path.write_text(content, encoding="utf-8")
        return {"ok": True, "operation": op, "file": path.name, "bytes": len(content.encode("utf-8"))}

    if op == "delete":
        if not path.exists():
            return {"ok": False, "error": f"File not found: {path.name}"}
        path.unlink()
        return {"ok": True, "operation": op, "file": path.name}

    return {
        "ok": False,
        "error": "Invalid operation. Use one of: create, read, update, delete",
    }


@mcp.tool()
def save_ownership_report(company_name: str, report_text: str) -> dict[str, Any]:
    """
    Write ``report_text`` to ``data/<sanitized_company>_ownership.txt``.
    Creates the file or overwrites if it already exists (same behaviour as demo).
    """
    fn = _default_ownership_filename(company_name)
    try:
        path = _safe_file_path(fn)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    created = not path.exists()
    path.write_text(report_text, encoding="utf-8")
    op = "create" if created else "update"
    return {
        "ok": True,
        "operation": op,
        "file": path.name,
        "bytes": len(report_text.encode("utf-8")),
    }


@mcp.tool()
def list_saved_files() -> list[dict]:
    """Returns all saved ownership reports from the data/ folder."""
    result = []
    for f in sorted(DATA_DIR.glob("*.txt")):
        try:
            content = f.read_text(encoding="utf-8")[:5000]
        except Exception:
            content = ""
        result.append({
            "name": f.name,
            "label": f.stem.replace("_", " ").title(),
            "content": content,
        })
    return result


@mcp.tool(app=True)
def ownership_dashboard(
    filename: str = "",
    heading: str = "Ownership Dashboard",
) -> PrefabApp:
    """
    Prefab UI — intentionally simple and reliable:

    1. **Fetch & Save** — type a company, click the button. Wikipedia data is shown
       immediately below, and the report is written to ``data/<name>_ownership.txt``.

    2. **Saved reports** — every ``.txt`` file in ``data/`` is rendered **directly
       from disk** when this tool runs. There is no dropdown or refresh button
       (those broke inside Prefab). After saving a new company, **run this tool
       again** from the tool runner — the new file appears automatically.
    """
    saved_files = sorted(DATA_DIR.glob("*.txt"))

    # Fetch → save (only interactive / reactive part)
    # Flat state keys (res_company, res_summary, …) — nested ``res`` does not bind
    # reliably in the Prefab bridge; dot-paths on ``RESULT`` are evaluated when the
    # action runs and store plain strings in state.
    on_fetch_click = [
        SetState("status", "loading"),
        SetState("res_company", ""),
        SetState("res_source", ""),
        SetState("res_summary", ""),
        SetState("res_report", ""),
        CallTool(
            "fetch_company_ownership",
            arguments={"company_name": f"{STATE.company_input}"},
            on_success=[
                SetState("res_company", RESULT.company),
                SetState("res_source", RESULT.source_page),
                SetState("res_summary", RESULT.summary),
                SetState("res_report", RESULT.report_text),
                SetState("display_title", RESULT.company),
                SetState("status", "saving"),
                CallTool(
                    "save_ownership_report",
                    arguments={
                        "company_name": f"{STATE.company_input}",
                        "report_text": f"{STATE.res_report}",
                    },
                    on_success=[
                        SetState("status", "done"),
                        ShowToast(
                            "Saved. Run ownership_dashboard again to see it in the list below.",
                            variant="success",
                        ),
                    ],
                    on_error=[
                        SetState("status", "done"),
                        ShowToast(f"Save failed: {ERROR}", variant="error"),
                    ],
                ),
            ],
            on_error=[
                SetState("status", "error"),
                SetState("res_company", ""),
                SetState("res_summary", ""),
                ShowToast(f"Could not fetch: {ERROR}", variant="error"),
            ],
        ),
    ]

    with PrefabApp(
        css_class="max-w-3xl mx-auto p-6",
        state={
            "company_input": "",
            "status": "",
            "res_company": "",
            "res_source": "",
            "res_summary": "",
            "res_report": "",
            "display_title": heading,
        },
    ) as app:

        # ── Card 1: interactive fetch ─────────────────────────────────────────
        with Card():
            with CardHeader():
                CardTitle(STATE.display_title)
                Muted("Fetch from Wikipedia and save to data/")
            with CardContent():
                with Column(gap=4):
                    Muted(
                        "Use the exact Wikipedia article title when possible "
                        "(e.g. \"boAt Lifestyle\", \"Apple Inc.\", \"Tata Sons\"). "
                        "Generic words like \"boat\" open the wrong article."
                    )
                    with Row(gap=8, align="center"):
                        Input(
                            name="company_input",
                            placeholder="Company name as on Wikipedia…",
                            css_class="flex-1",
                        )
                        Button("Fetch & Save", on_click=on_fetch_click)

                    with If(STATE.status == "loading"):
                        Muted("Fetching…")
                    with If(STATE.status == "saving"):
                        Muted("Saving…")
                    with If(STATE.status == "error"):
                        Text("Fetch failed — check the toast or try a more specific name.")
                    with If(STATE.status == "done"):
                        with Column(gap=2):
                            H3(STATE.res_company)
                            Muted("Source: " + STATE.res_source)
                            Markdown(STATE.res_summary)

        # ── Card 2: static snapshot of disk (reliable — no reactive lists) ───
        with Card():
            with CardHeader():
                CardTitle(f"Reports on disk ({len(saved_files)})")
                Muted("Re-run this tool after each Fetch & Save to reload from disk.")
            with CardContent():
                with Column(gap=6):
                    if not saved_files:
                        Muted("No .txt files in data/ yet.")
                    else:
                        for path in saved_files:
                            try:
                                body = path.read_text(encoding="utf-8")
                            except OSError:
                                body = "(could not read file)"
                            if len(body) > 4500:
                                body = body[:4500] + "\n\n… (truncated)"
                            H3(path.stem.replace("_", " ").title())
                            Muted(path.name)
                            Text(body)
                            Separator()

    return app


if __name__ == "__main__":
    mcp.run()
