import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from mcp import types


sys.path.insert(0, str(Path(__file__).resolve().parents[1]) if __file__ != "<stdin>" else "/broker")
import server


class InventoryTests(unittest.TestCase):
    def test_scan_is_bounded_and_refuses_links(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(server, "NAS", Path(directory)):
            movies = Path(directory) / "media" / "Movies"
            movies.mkdir(parents=True)
            title = movies / "A Film"
            title.mkdir()
            (title / "video.mkv").write_bytes(b"test")
            (movies / "linked").symlink_to(title, target_is_directory=True)
            self.assertEqual(server.read_inventory("movies")["matches_in_scan"], 1)
            self.assertEqual(server.read_inventory("movies", folder="A Film")["entries"][0]["bytes"], 4)
            with self.assertRaises(ValueError):
                server.read_inventory("movies", folder="..")
            with self.assertRaises(OSError):
                server.read_inventory("movies", folder="linked")
            with patch.object(server, "SCAN_BUDGET", 1):
                self.assertTrue(server.read_inventory("movies")["scan_truncated"])


class ToolBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_upstream_lists_keep_empty_and_singleton_shapes(self):
        session = AsyncMock()
        session.call_tool.return_value = types.CallToolResult(content=[])
        self.assertEqual(await server.call_upstream(session, "radarr_queue"), [])
        session.call_tool.return_value = types.CallToolResult(content=[
            types.TextContent(type="text", text='{"key":"1"}'),
        ])
        self.assertEqual(await server.call_upstream(session, "plex_libraries"), [{"key": "1"}])
        self.assertEqual(await server.call_upstream(session, "media_overview"), {"key": "1"})

    async def test_unsupported_and_out_of_scope_calls_never_reach_upstream(self):
        with patch.object(server, "upstream") as upstream:
            for name, arguments in (
                ("filebot_rename", {}),
                ("plex_scan_library", {"section_key": "invalid"}),
                ("radarr_add", {"tmdb_id": 1, "root_folder": "/not-approved"}),
                ("media_workloads", {"namespace": "kube-system"}),
            ):
                self.assertIs((await server.call_tool(name, arguments)).isError, True)
            upstream.assert_not_called()

    def test_sensitive_fields_and_urls_are_not_forwarded(self):
        output = server.result({"accessToken": "private", "endpoint": "https://example.invalid/?key=private",
                                "title": "A Film"})
        self.assertEqual(json.loads(output.content[0].text), {"endpoint": "[link withheld]", "title": "A Film"})


if __name__ == "__main__":
    unittest.main()
