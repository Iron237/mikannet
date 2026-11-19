"""Expose reviewed Mikannet operations over MCP stdio."""
import argparse
import asyncio
import os
import re
from urllib.parse import quote, urlsplit

import httpx
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from .catalog import BODIES, PREFERENCES, TOOLS


def expand(schema, document):
    if isinstance(schema, list):
        return [expand(item, document) for item in schema]
    if not isinstance(schema, dict):
        return schema
    if "$ref" in schema:
        prefix = "#/components/schemas/"
        ref = schema["$ref"]
        if not ref.startswith(prefix):
            raise ValueError("Unsupported OpenAPI reference: " + ref)
        return expand(document["components"]["schemas"][ref[len(prefix):]], document)
    return {key: expand(value, document) for key, value in schema.items()}


def input_schema(name, operation, spec, document):
    fields = {}
    required = []
    for param in spec.get("parameters", []):
        key = param["name"]
        if name == "save_danmaku" and key == "browser":
            continue  # Agents receive the saved XML reference; browser=true returns binary.
        if param["in"] not in ("path", "query"):
            raise ValueError(f"Unsupported parameter location on {name}")
        fields[key] = expand(param["schema"], document)
        if param.get("required"):
            required.append(key)
    if "requestBody" in spec:
        request = spec["requestBody"]
        model = expand(request["content"]["application/json"]["schema"], document)
        fields["body"] = BODIES.get(name, model)
        if fields["body"].get("type") == "object":
            fields["body"] = {**fields["body"], "additionalProperties": False}
        if name == "scan_anime" or request.get("required"):
            required.append("body")
    if operation.method == "GET":
        fields.update({
            "page_size": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20,
                          "description": "每个顶层列表最多返回的条数。"},
            "offset": {"type": "integer", "minimum": 0, "default": 0},
            "section": {"type": "string", "description": "只返回指定顶层列表，如 episodes 或 subgroups。"},
        })
    return {"type": "object", "properties": fields, "required": required, "additionalProperties": False}


def clean(data):
    if isinstance(data, list):
        return [clean(i) for i in data]
    if isinstance(data, dict):
        return {k: clean(v) for k, v in data.items()
                if not re.search(r"password|secret|token|cookie|credentials|api_key", k, re.I)
                and not (isinstance(v, str) and v.startswith(("mikannet://", "mikanarr://")))}
    if isinstance(data, str):
        data = re.sub(r"(?i)(https?://)[^/\s@]+@", r"\1***@", data)
        return re.sub(r"(?i)([?&](?:access_token|token|auth|key|passkey|api_key)=)[^&#\s]+", r"\1***", data)
    return data


def page(data, args):
    section = args.get("section")
    if section:
        if not isinstance(data, dict) or not isinstance(data.get(section), list):
            raise ValueError("所选 section 不是结果里的列表")
        data = data[section]
    offset, size = args.get("offset", 0), args.get("page_size", 20)
    result = data.copy() if isinstance(data, dict) else {"items": data}
    pages = {}
    for key, value in result.items():
        if isinstance(value, list):
            end = offset + size
            result[key] = value[offset:end]
            pages[key] = {"total": len(value), "offset": offset, "next_offset": end if end < len(value) else None}
    return {"data": clean(result), "pages": pages}


def create_server(client, document, read_only):
    server = Server("mikannet", instructions="先用查询工具取得数字 ID。本地 bangumi_id 与 mikan_bangumi_id 不能互换。按 pages.next_offset 继续读取。")
    available = {}
    for name, operation in TOOLS.items():
        spec = document["paths"].get(operation.path, {}).get(operation.method.lower())
        if spec is None or (read_only and operation.method != "GET"):
            continue
        available[name] = types.Tool(name=name, description=operation.description,
            inputSchema=input_schema(name, operation, spec, document),
            annotations=types.ToolAnnotations(readOnlyHint=operation.method == "GET"))

    @server.list_tools()
    async def list_tools():
        return list(available.values())

    @server.call_tool()
    async def call_tool(name, arguments):
        if name not in available:
            raise ValueError("工具不存在或当前模式不允许")
        op = TOOLS[name]
        spec = document["paths"][op.path][op.method.lower()]
        path, query = op.path, {}
        for param in spec.get("parameters", []):
            key = param["name"]
            if param["in"] == "path":
                path = path.replace("{" + key + "}", quote(str(arguments[key]), safe=""))
            elif arguments.get(key) is not None:
                query[key] = arguments[key]
        try:
            response = await client.request(op.method, path, params=query, json=arguments.get("body"))
        except httpx.RequestError as exc:
            raise ValueError("无法确认 Mikannet 请求结果，请检查服务连接及当前任务状态") from exc
        if not response.is_success:
            try:
                detail = clean(response.json().get("detail", response.reason_phrase))
            except (ValueError, AttributeError):
                detail = response.reason_phrase
            raise ValueError(f"Mikannet HTTP {response.status_code}: {str(detail)[:600]}")
        if response.status_code == 204 or not response.content:
            return {"status": response.status_code}
        data = response.json()
        if name == "get_preferences":
            data = {key: data[key]["value"] for key in PREFERENCES if key in data}
        result = page(data, arguments) if op.method == "GET" else {"data": clean(data)}
        return {"status": response.status_code, **result}

    return server


async def serve(base_url, read_only):
    origin = urlsplit(base_url)
    if (origin.scheme not in ("http", "https") or not origin.hostname or origin.username is not None
            or origin.path not in ("", "/") or origin.query or origin.fragment):
        raise ValueError("base-url 必须是 Mikannet 的 HTTP(S) 服务地址，不包含路径、凭据或查询参数")
    async with httpx.AsyncClient(base_url=base_url, trust_env=False, timeout=httpx.Timeout(120, connect=10)) as client:
        response = await client.get("/openapi.json")
        response.raise_for_status()
        document = response.json()
        if document.get("info", {}).get("title") != "Mikannet":
            raise ValueError("目标服务没有提供 Mikannet OpenAPI")
        server = create_server(client, document, read_only)
        async with stdio_server() as (reader, writer):
            await server.run(reader, writer, server.create_initialization_options())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("MIKANNET_BASE_URL", "http://127.0.0.1:8008"))
    parser.add_argument("--read-only", action="store_true", help="只注册读取工具")
    args = parser.parse_args()
    asyncio.run(serve(args.base_url, args.read_only))


if __name__ == "__main__":
    main()
