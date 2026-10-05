"""Connect, list tools, and optionally save a screenshot without sending input."""

import argparse
import asyncio
import base64
import json
import os
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8765/mcp")
    parser.add_argument("--capture", type=Path)
    args = parser.parse_args()
    token = os.environ.get("FULLREMOTE_TOKEN")
    if not token:
        parser.error("Set FULLREMOTE_TOKEN to the VM server's token")
    async with streamablehttp_client(args.url, headers={"Authorization": f"Bearer {token}"}) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            tools = await session.list_tools()
            print(json.dumps({"tools": [tool.name for tool in tools.tools]}, indent=2))
            info = await session.call_tool("system_info", {})
            if info.isError:
                raise RuntimeError(info.content)
            for content in info.content:
                if content.type == "text":
                    print(content.text)
            if args.capture:
                result = await session.call_tool("observe", {"max_size": 1600})
                if result.isError:
                    raise RuntimeError(result.content)
                images = [content for content in result.content if content.type == "image"]
                if not images:
                    raise RuntimeError("observe did not return an image")
                args.capture.write_bytes(base64.b64decode(images[0].data))
                print(f"Saved screenshot: {args.capture.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())
