"""Verify a real MCP stdio handshake against the packaged server.

Run using the project's .venv interpreter after setup. No database credentials
or external services are required.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


async def main() -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / 'scripts' / 'mcp_server.py')],
        cwd=str(ROOT),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)
            tools = await asyncio.wait_for(session.list_tools(), timeout=30)
            names = {tool.name for tool in tools.tools}
            required = {'db_capabilities', 'db_connect_env', 'db_export', 'db_preview_export'}
            missing = required - names
            if missing:
                raise RuntimeError(f'MCP is missing required tools: {sorted(missing)}')
            result = await asyncio.wait_for(session.call_tool('db_capabilities', {}), timeout=30)
            if result.isError:
                raise RuntimeError('MCP db_capabilities returned an error')
            print(f'MCP startup OK: {len(names)} tools, basic capability handshake passed')


if __name__ == '__main__':
    asyncio.run(main())
