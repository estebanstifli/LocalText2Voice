"""Thin typed stdio tools backed by the shared headless service API."""
import inspect
from typing import Any

from app.server.storyboard_service import StoryboardService


def register_storyboard_tools(mcp, request_json):
    for name, method in inspect.getmembers(StoryboardService, inspect.isfunction):
        if not name.startswith("sb_"):
            continue
        signature = inspect.signature(method, eval_str=True)
        signature = signature.replace(parameters=[p for p in signature.parameters.values() if p.name != "self"], return_annotation=Any)

        def make_tool(operation, schema, description):
            def invoke(**arguments):
                payload = request_json("POST", f"/storyboard/{operation}", arguments, timeout=120)
                if operation == "sb_preview":
                    import base64
                    from mcp.server.fastmcp import Image
                    return Image(data=base64.b64decode(payload["image_base64"]), format="jpeg")
                return payload
            invoke.__name__ = operation
            invoke.__doc__ = description
            invoke.__signature__ = schema
            invoke.__annotations__ = {p.name: p.annotation for p in schema.parameters.values()}
            invoke.__annotations__["return"] = Any
            return invoke

        mcp.add_tool(make_tool(name, signature, method.__doc__), name=name, description=method.__doc__ or name)
