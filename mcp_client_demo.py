"""Call server.py over MCP: list the tools, then call get_subscription_usage. Paste into the notebook."""
import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    params = StdioServerParameters(command=sys.executable, args=["server.py"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("Tools:", [t.name for t in tools.tools])
            for args in ({"time_period": "monthly", "unused_only": True},
                         {"time_period": "yearly", "subscription_name": "Slack"}):
                result = await session.call_tool("get_subscription_usage", args)
                print(args, "->", result.content[0].text)


asyncio.run(main())
