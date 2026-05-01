from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen

from fastmcp.client import Client


GEMINI_MODEL = "gemini-2.5-flash-lite"


def _pretty(obj: object) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=True)


def _default_filename(company: str) -> str:
    """``<sanitized_lowercase>_ownership.txt`` (e.g. Tata Sons → tata_sons_ownership.txt)."""
    safe = re.sub(r"[^\w\s.-]", "", company.strip(), flags=re.UNICODE)
    safe = re.sub(r"\s+", "_", safe).strip("_").lower() or "report"
    return f"{safe}_ownership.txt"


def _extract_json_object(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return {}
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}


def _load_env_file() -> None:
    """Load simple KEY=VALUE pairs from .env into os.environ (without overriding existing vars)."""
    env_path = Path(__file__).with_name(".env")
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _call_gemini(api_key: str, history: list[dict[str, str]], query: str) -> dict[str, Any]:
    prompt = "\n".join(
        [
            "You are a planning assistant.",
            "Return JSON only with keys: reasoning, tool_name, tool_args.",
            "Allowed tool_name: fetch_company_ownership, local_file_crud, ownership_dashboard.",
            "Include only one tool per response.",
            "",
            "ALL_PAST_INTERACTIONS:",
            json.dumps(history, ensure_ascii=True),
            "",
            "CURRENT_QUERY:",
            query,
        ]
    )
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={quote(api_key)}"
    )
    req = Request(
        url=url,
        method="POST",
        headers={"Content-Type": "application/json"},
        data=json.dumps(
            {
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.1,
                    "responseMimeType": "application/json",
                },
            }
        ).encode("utf-8"),
    )
    with urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    text = (
        payload.get("candidates", [{}])[0]
        .get("content", {})
        .get("parts", [{}])[0]
        .get("text", "{}")
    )
    return _extract_json_object(text)


async def _run_flow_direct(company: str, filename: str, heading: str) -> None:
    # For stdio MCP servers, the client launches the server process itself.
    server_path = str(Path(__file__).with_name("server.py"))

    async with Client(server_path) as client:
        tools = await client.list_tools()
        print("Connected tools:", ", ".join(t.name for t in tools))

        internet = await client.call_tool(
            "fetch_company_ownership", {"company_name": company}
        )
        if internet.is_error:
            raise RuntimeError(f"Internet tool failed: {internet.content}")
        internet_data = internet.data or {}
        print("\n[1] Internet tool result")
        print(_pretty(internet_data))

        report_text = internet_data.get("report_text") or internet_data.get("summary", "")
        create = await client.call_tool(
            "local_file_crud",
            {
                "operation": "create",
                "filename": filename,
                "content": report_text,
            },
            raise_on_error=False,
        )
        create_data = create.data or {}
        if create_data.get("ok") is False and "already exists" in str(create_data.get("error", "")).lower():
            create = await client.call_tool(
                "local_file_crud",
                {
                    "operation": "update",
                    "filename": filename,
                    "content": report_text,
                },
            )
            create_data = create.data or {}

        print("\n[2] CRUD write result")
        print(_pretty(create_data))

        read_back = await client.call_tool(
            "local_file_crud",
            {"operation": "read", "filename": filename},
        )
        read_data = read_back.data or {}
        print("\n[3] CRUD read result")
        print(_pretty(read_data))

        ui = await client.call_tool(
            "ownership_dashboard",
            {"filename": filename, "heading": heading},
        )
        print("\n[4] UI tool called successfully")
        print("UI response type:", type(ui).__name__)
        print(
            "Note: To visually render Prefab UI, use `fastmcp dev apps server.py` "
            "and launch `ownership_dashboard` in the web interface."
        )


async def _run_flow_llm(company: str, filename: str, heading: str, gemini_api_key: str) -> None:
    server_path = str(Path(__file__).with_name("server.py"))
    history: list[dict[str, str]] = []
    trace: list[dict[str, Any]] = []

    async with Client(server_path) as client:
        tools = await client.list_tools()
        print("Connected tools:", ", ".join(t.name for t in tools))
        print(f"LLM planner model: {GEMINI_MODEL}")

        q1 = (
            f"Step 1: choose tool to fetch ownership data for company '{company}'. "
            "Return tool args."
        )
        step1 = await asyncio.to_thread(_call_gemini, gemini_api_key, history, q1)
        args1 = {"company_name": company, **(step1.get("tool_args") or {})}
        tool1 = step1.get("tool_name") or "fetch_company_ownership"
        res1 = await client.call_tool(tool1, args1)
        if res1.is_error:
            raise RuntimeError(f"Step 1 failed: {res1.content}")
        data1 = res1.data or {}
        s1 = {"query": q1, "llm_response": step1, "tool_call": {"name": tool1, "args": args1}, "tool_result": data1}
        trace.append(s1)
        print("\n[1] Internet tool result")
        print(_pretty(s1))
        history.extend(
            [
                {"role": "user", "content": q1},
                {"role": "assistant", "content": json.dumps(step1, ensure_ascii=True)},
                {"role": "tool", "content": json.dumps(data1, ensure_ascii=True)},
            ]
        )

        report_text = data1.get("report_text") or data1.get("summary", "")
        q2 = (
            f"Step 2: choose CRUD tool call to save report to filename '{filename}'. "
            "Prefer operation=create."
        )
        step2 = await asyncio.to_thread(_call_gemini, gemini_api_key, history, q2)
        llm2 = step2.get("tool_args") or {}
        args2 = {
            "operation": llm2.get("operation", "create"),
            "filename": llm2.get("filename", filename),
            "content": llm2.get("content", report_text),
        }
        tool2 = step2.get("tool_name") or "local_file_crud"
        res2 = await client.call_tool(tool2, args2, raise_on_error=False)
        data2 = res2.data or {}
        if data2.get("ok") is False and "already exists" in str(data2.get("error", "")).lower():
            args2 = {"operation": "update", "filename": filename, "content": report_text}
            res2 = await client.call_tool("local_file_crud", args2)
            data2 = res2.data or {}
        s2 = {"query": q2, "llm_response": step2, "tool_call": {"name": tool2, "args": args2}, "tool_result": data2}
        trace.append(s2)
        print("\n[2] CRUD write result")
        print(_pretty(s2))
        history.extend(
            [
                {"role": "user", "content": q2},
                {"role": "assistant", "content": json.dumps(step2, ensure_ascii=True)},
                {"role": "tool", "content": json.dumps(data2, ensure_ascii=True)},
            ]
        )

        q3 = f"Step 3: choose CRUD read call for filename '{filename}'."
        step3 = await asyncio.to_thread(_call_gemini, gemini_api_key, history, q3)
        llm3 = step3.get("tool_args") or {}
        args3 = {
            "operation": "read",
            "filename": llm3.get("filename", filename),
        }
        tool3 = step3.get("tool_name") or "local_file_crud"
        res3 = await client.call_tool(tool3, args3)
        data3 = res3.data or {}
        s3 = {"query": q3, "llm_response": step3, "tool_call": {"name": tool3, "args": args3}, "tool_result": data3}
        trace.append(s3)
        print("\n[3] CRUD read result")
        print(_pretty(s3))
        history.extend(
            [
                {"role": "user", "content": q3},
                {"role": "assistant", "content": json.dumps(step3, ensure_ascii=True)},
                {"role": "tool", "content": json.dumps(data3, ensure_ascii=True)},
            ]
        )

        q4 = (
            "Step 4: choose UI tool call to render Prefab dashboard for the same file and heading."
        )
        step4 = await asyncio.to_thread(_call_gemini, gemini_api_key, history, q4)
        llm4 = step4.get("tool_args") or {}
        args4 = {
            "filename": llm4.get("filename", filename),
            "heading": llm4.get("heading", heading),
        }
        tool4 = step4.get("tool_name") or "ownership_dashboard"
        ui = await client.call_tool(tool4, args4)
        s4 = {"query": q4, "llm_response": step4, "tool_call": {"name": tool4, "args": args4}, "tool_result": "PrefabApp returned"}
        trace.append(s4)
        print("\n[4] UI tool called successfully")
        print(_pretty(s4))
        print("UI response type:", type(ui).__name__)
        print("\nReasoning trace (all steps):")
        print(_pretty(trace))
        print(
            "Note: To visually render Prefab UI, use `fastmcp dev apps server.py` "
            "and launch `ownership_dashboard` in the web interface."
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MCP client demo for Assignment 4 server")
    parser.add_argument("--company", required=True, help="Company name to fetch")
    parser.add_argument(
        "--filename",
        default=None,
        help="Local file under data/ (default: derived from --company, e.g. Philips → philips_ownership.txt)",
    )
    parser.add_argument(
        "--heading",
        default=None,
        help="Dashboard heading (default: same as --company)",
    )
    parser.add_argument(
        "--use-llm",
        action="store_true",
        help="Use Gemini planner (gemini-2.5-flash-lite) to choose each tool call.",
    )
    parser.add_argument(
        "--gemini-api-key",
        default=None,
        help="Gemini API key (or set GEMINI_API_KEY env var).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    _load_env_file()
    args = parse_args()
    company = args.company.strip()
    filename = args.filename or _default_filename(company)
    heading = (args.heading or company).strip()
    if args.use_llm:
        api_key = (args.gemini_api_key or os.getenv("GEMINI_API_KEY", "")).strip()
        if not api_key:
            raise SystemExit(
                "Gemini key missing. Pass --gemini-api-key or set GEMINI_API_KEY."
            )
        asyncio.run(_run_flow_llm(company, filename, heading, api_key))
    else:
        asyncio.run(_run_flow_direct(company, filename, heading))
