from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen

import streamlit as st
from fastmcp.client import Client


GEMINI_MODEL = "gemini-2.5-flash-lite"


def _load_env_file() -> None:
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


def _default_filename(company: str) -> str:
    safe = re.sub(r"[^\w\s.-]", "", company.strip(), flags=re.UNICODE)
    safe = re.sub(r"\s+", "_", safe).strip("_").lower() or "report"
    return f"{safe}_ownership.txt"


async def _run_direct(company: str, filename: str, heading: str) -> dict[str, Any]:
    server_path = str(Path(__file__).with_name("server.py"))
    trace: list[dict[str, Any]] = []
    async with Client(server_path) as client:
        internet = await client.call_tool("fetch_company_ownership", {"company_name": company})
        if internet.is_error:
            raise RuntimeError(f"Internet tool failed: {internet.content}")
        internet_data = internet.data or {}
        trace.append({"step": 1, "tool": "fetch_company_ownership", "result": internet_data})

        report_text = internet_data.get("report_text") or internet_data.get("summary", "")
        write = await client.call_tool(
            "local_file_crud",
            {"operation": "create", "filename": filename, "content": report_text},
            raise_on_error=False,
        )
        write_data = write.data or {}
        if write_data.get("ok") is False and "already exists" in str(write_data.get("error", "")).lower():
            write = await client.call_tool(
                "local_file_crud",
                {"operation": "update", "filename": filename, "content": report_text},
            )
            write_data = write.data or {}
        trace.append({"step": 2, "tool": "local_file_crud(write)", "result": write_data})

        read_back = await client.call_tool("local_file_crud", {"operation": "read", "filename": filename})
        read_data = read_back.data or {}
        trace.append({"step": 3, "tool": "local_file_crud(read)", "result": read_data})

        ui = await client.call_tool("ownership_dashboard", {"filename": filename, "heading": heading})
        trace.append({"step": 4, "tool": "ownership_dashboard", "result": {"type": type(ui).__name__}})

    return {"trace": trace, "file": filename}


async def _run_llm(company: str, filename: str, heading: str, api_key: str) -> dict[str, Any]:
    server_path = str(Path(__file__).with_name("server.py"))
    history: list[dict[str, str]] = []
    trace: list[dict[str, Any]] = []
    async with Client(server_path) as client:
        q1 = f"Step 1: choose tool to fetch ownership for '{company}'."
        s1 = await asyncio.to_thread(_call_gemini, api_key, history, q1)
        args1 = {"company_name": company, **(s1.get("tool_args") or {})}
        t1 = s1.get("tool_name") or "fetch_company_ownership"
        r1 = await client.call_tool(t1, args1)
        d1 = r1.data or {}
        trace.append({"query": q1, "llm": s1, "tool": t1, "args": args1, "result": d1})
        history.extend(
            [
                {"role": "user", "content": q1},
                {"role": "assistant", "content": json.dumps(s1, ensure_ascii=True)},
                {"role": "tool", "content": json.dumps(d1, ensure_ascii=True)},
            ]
        )

        report_text = d1.get("report_text") or d1.get("summary", "")
        q2 = f"Step 2: choose CRUD tool call to save report in '{filename}'."
        s2 = await asyncio.to_thread(_call_gemini, api_key, history, q2)
        t2 = s2.get("tool_name") or "local_file_crud"
        a2raw = s2.get("tool_args") or {}
        args2 = {
            "operation": a2raw.get("operation", "create"),
            "filename": a2raw.get("filename", filename),
            "content": a2raw.get("content", report_text),
        }
        r2 = await client.call_tool(t2, args2, raise_on_error=False)
        d2 = r2.data or {}
        if d2.get("ok") is False and "already exists" in str(d2.get("error", "")).lower():
            args2 = {"operation": "update", "filename": filename, "content": report_text}
            r2 = await client.call_tool("local_file_crud", args2)
            d2 = r2.data or {}
        trace.append({"query": q2, "llm": s2, "tool": t2, "args": args2, "result": d2})
        history.extend(
            [
                {"role": "user", "content": q2},
                {"role": "assistant", "content": json.dumps(s2, ensure_ascii=True)},
                {"role": "tool", "content": json.dumps(d2, ensure_ascii=True)},
            ]
        )

        q3 = f"Step 3: choose CRUD read call for '{filename}'."
        s3 = await asyncio.to_thread(_call_gemini, api_key, history, q3)
        t3 = s3.get("tool_name") or "local_file_crud"
        args3 = {"operation": "read", "filename": (s3.get("tool_args") or {}).get("filename", filename)}
        r3 = await client.call_tool(t3, args3)
        d3 = r3.data or {}
        trace.append({"query": q3, "llm": s3, "tool": t3, "args": args3, "result": d3})
        history.extend(
            [
                {"role": "user", "content": q3},
                {"role": "assistant", "content": json.dumps(s3, ensure_ascii=True)},
                {"role": "tool", "content": json.dumps(d3, ensure_ascii=True)},
            ]
        )

        q4 = "Step 4: choose Prefab UI call."
        s4 = await asyncio.to_thread(_call_gemini, api_key, history, q4)
        t4 = s4.get("tool_name") or "ownership_dashboard"
        a4raw = s4.get("tool_args") or {}
        args4 = {"filename": a4raw.get("filename", filename), "heading": a4raw.get("heading", heading)}
        ui = await client.call_tool(t4, args4)
        trace.append({"query": q4, "llm": s4, "tool": t4, "args": args4, "result": {"type": type(ui).__name__}})
    return {"trace": trace, "file": filename}


def main() -> None:
    _load_env_file()
    st.set_page_config(page_title="MCP Ownership Demo", page_icon=":bar_chart:")
    st.title("MCP Ownership Demo")
    st.caption("User input UI (Streamlit) + MCP tools + Prefab dashboard tool call")

    company = st.text_input("Company name", placeholder="Tata Sons")
    heading = st.text_input("Dashboard heading (optional)", placeholder="Ownership Dashboard")
    use_llm = st.checkbox("Use Gemini planner (gemini-2.5-flash-lite)", value=False)
    api_key = st.text_input(
        "Gemini API key (optional if set in .env)",
        value=os.getenv("GEMINI_API_KEY", ""),
        type="password",
    )

    if st.button("Run flow", type="primary"):
        company = company.strip()
        if not company:
            st.error("Please enter a company name.")
            return
        filename = _default_filename(company)
        safe_heading = (heading or company).strip()
        with st.spinner("Running MCP flow..."):
            try:
                if use_llm:
                    if not api_key.strip():
                        st.error("Gemini key missing. Add GEMINI_API_KEY to .env or enter above.")
                        return
                    output = asyncio.run(_run_llm(company, filename, safe_heading, api_key.strip()))
                else:
                    output = asyncio.run(_run_direct(company, filename, safe_heading))
            except Exception as exc:
                st.exception(exc)
                return
        st.success("Flow completed.")
        st.write(f"Saved file: `{output['file']}`")
        st.json(output["trace"])
        st.info("To visually render Prefab UI, run `fastmcp dev apps server.py` and launch `ownership_dashboard`.")


if __name__ == "__main__":
    main()
