import json
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from mcp_client import McpClient, McpError, McpTimeout


class McpClientTests(unittest.TestCase):
    def client(self, mode="normal", timeout=5):
        return McpClient([sys.executable, str(ROOT / "tests" / "fake_mcp.py"), mode], timeout=timeout)

    def test_handshake_and_listing(self):
        with self.client() as client:
            self.assertEqual(client.server_info["name"], "fake")
            self.assertEqual(client.list_tools()[0]["name"], "test_tool")
        self.assertIsNotNone(client.process.poll())

    def test_binary_stderr_is_drained_without_blocking_handshake(self):
        with self.client("stderr-binary") as client:
            self.assertEqual(len(client.list_tools()), 1)

    def test_invalid_utf8_stdout_is_rejected(self):
        with self.assertRaisesRegex(McpError, "JSON invalid"):
            with self.client("stdout-binary"):
                pass

    def test_descendant_pipe_handles_do_not_block_close(self):
        client = self.client("orphan-pipes")
        client.start()
        client.call_tool("test_tool", {})
        started = time.monotonic()
        client.close()
        self.assertLess(time.monotonic() - started, 5)
        self.assertTrue(all(not thread.is_alive() for thread in client._threads))

    def test_arguments_round_trip_unicode_and_quotes(self):
        arguments = {"text": 'Știință, "ghilimele", \\ și\nlinie', "number": 2}
        with self.client() as client:
            result = client.call_tool("test_tool", arguments)
            text = json.loads(result["content"][0]["text"])
            self.assertEqual(text["arguments"], arguments)

    def test_pagination(self):
        with self.client("pagination") as client:
            self.assertEqual([tool["name"] for tool in client.list_tools()], ["first", "second"])

    def test_repeated_cursor_is_rejected(self):
        with self.client("repeated-cursor") as client:
            with self.assertRaisesRegex(McpError, "Paginarea"):
                client.list_tools()

    def test_invalid_tool_list(self):
        with self.client("bad-tools") as client:
            with self.assertRaisesRegex(McpError, "Lista instrumentelor"):
                client.list_tools()

    def test_notifications_do_not_break_requests(self):
        with self.client("notification") as client:
            self.assertEqual(len(client.list_tools()), 1)

    def test_unsolicited_sampling_is_refused(self):
        with self.client("sampling") as client:
            client.list_tools()
            result = client.call_tool("test_tool", {})
            self.assertTrue(json.loads(result["content"][0]["text"])["refusedSampling"])

    def test_rpc_error_is_not_success(self):
        with self.client("rpc-error") as client:
            with self.assertRaisesRegex(McpError, "-32602"):
                client.call_tool("test_tool", {})

    def test_tool_error_flag_is_preserved(self):
        with self.client("tool-error") as client:
            self.assertTrue(client.call_tool("test_tool", {})["isError"])

    def test_malformed_json(self):
        with self.assertRaisesRegex(McpError, "JSON invalid"):
            with self.client("malformed"):
                pass

    def test_unsupported_protocol(self):
        with self.assertRaisesRegex(McpError, "nesuportată"):
            with self.client("protocol"):
                pass

    def test_eof(self):
        with self.assertRaisesRegex(McpError, "închis conexiunea"):
            with self.client("eof"):
                pass

    def test_timeout_closes_own_process(self):
        client = self.client("timeout", timeout=0.25)
        with self.assertRaises(McpTimeout):
            client.start()
        self.assertIsNotNone(client.process.poll())

    def test_invalid_timeout(self):
        with self.assertRaises(ValueError):
            self.client(timeout=0)

    def test_send_before_start(self):
        with self.assertRaisesRegex(McpError, "nu este deschisă"):
            self.client().request("tools/list", {})


if __name__ == "__main__":
    unittest.main()
