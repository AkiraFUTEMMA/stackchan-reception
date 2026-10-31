"""Public MCP registration and read-only request handling."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

from test_reception import app


class PublicApiTests(unittest.TestCase):
    def test_only_current_tools_are_registered_without_provider_internals(self):
        class RecordingMCP:
            def __init__(self, name):
                self.tools = {}

            def tool(self):
                def register(function):
                    self.tools[function.__name__] = function
                    return function
                return register

        fake = types.ModuleType("fastmcp")
        fake.FastMCP = RecordingMCP
        spec = importlib.util.spec_from_file_location("public_registration_test", app.__file__)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"fastmcp": fake}):
            spec.loader.exec_module(module)
        self.assertEqual(set(module.mcp.tools), {
            "advance_reception", "get_center_info", "get_place_guidance",
            "get_training_guidance", "get_facility_policy",
        })

    def test_reception_request_does_not_write_local_files(self):
        original_open = Path.open

        def read_only(path, mode="r", *args, **kwargs):
            if any(flag in mode for flag in "wax+"):
                raise AssertionError("A reception request must not write local files")
            return original_open(path, mode, *args, **kwargs)

        with patch.object(Path, "open", read_only), patch.object(
                app.mcp, "local_provider", object(), create=True):
            result = app.advance_reception("森川さんに荷物です")
        self.assertEqual(result["action"], "ask_name")


if __name__ == "__main__":
    unittest.main()
