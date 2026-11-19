from pathlib import Path
import json
import sys

from app.main import app
from jsonschema.validators import validator_for

from mikannet_mcp.catalog import TOOLS
from mikannet_mcp.server import input_schema

CONTRACT = Path(__file__).with_name("tool-inputs.jsonl")


def semantic_schema(value):
    if isinstance(value, list):
        return [semantic_schema(item) for item in value]
    if isinstance(value, dict):
        fields = {k: semantic_schema(v) for k, v in value.items()
                  if k not in ("title", "description", "examples", "properties")}
        if "properties" in value:
            fields["properties"] = {name: semantic_schema(s) for name, s in value["properties"].items()}
        return fields
    return value


def render_inputs():
    document = app.openapi()
    lines = []
    for name, op in sorted(TOOLS.items()):
        operation = document["paths"][op.path][op.method.lower()]
        schema = input_schema(name, op, operation, document)
        # The shared pagination contract is exercised through the stdio client.
        fields = {k: v for k, v in schema["properties"].items() if k not in ("offset", "section", "page_size")}
        entry = {"tool": name, "required": schema["required"],
                 "fields": {k: semantic_schema(v) for k, v in fields.items()}}
        lines.append(json.dumps(entry, sort_keys=True, ensure_ascii=False))
    return "\n".join(lines) + "\n"


def test_published_tool_inputs():
    assert CONTRACT.read_text("utf-8") == render_inputs()


def test_every_rest_operation_has_an_agent_decision():
    spec = app.openapi()
    actual = {(m.upper(), p) for p, methods in spec["paths"].items() if p.startswith("/api/")
              for m in methods if m in {"get", "post", "patch", "put", "delete", "head", "options", "trace"}}
    exposed = {(op.method, op.path) for op in TOOLS.values()}
    lines = (Path(__file__).resolve().parents[1] / "web-only.txt").read_text("utf-8").splitlines()
    web_only = {tuple(line.split()) for line in lines if line and not line.startswith("#")}
    assert not exposed & web_only
    assert len(exposed) == len(TOOLS)
    assert actual == exposed | web_only
    for name, op in TOOLS.items():
        operation = spec["paths"][op.path][op.method.lower()]
        schema = input_schema(name, op, operation, spec)
        validator_for(schema).check_schema(schema)
        if "requestBody" in operation:
            body = schema["properties"]["body"]
            assert body["type"] == "object" and body["properties"] and not body["additionalProperties"], name


if __name__ == "__main__":
    if sys.argv[1:] != ["--update"]:
        raise SystemExit("Use --update to regenerate the input contract for review.")
    CONTRACT.write_text(render_inputs(), encoding="utf-8")
