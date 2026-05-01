from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path

from fastmcp.client import Client


def _pretty(obj: object) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=True)


def _default_filename(company: str) -> str:
    """``<sanitized_lowercase>_ownership.txt`` (e.g. Tata Sons → tata_sons_ownership.txt)."""
    safe = re.sub(r"[^\w\s.-]", "", company.strip(), flags=re.UNICODE)
    safe = re.sub(r"\s+", "_", safe).strip("_").lower() or "report"
    return f"{safe}_ownership.txt"


async def run_flow(company: str, filename: str, heading: str) -> None:
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MCP client demo for Assignment 4 server")
    parser.add_argument("--company", default="Tata Sons", help="Company name to fetch")
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
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    company = args.company.strip()
    filename = args.filename or _default_filename(company)
    heading = (args.heading or company).strip()
    asyncio.run(run_flow(company, filename, heading))
