from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock

from bacchus import headless
from bacchus.runtime import running_instance


class HeadlessTests(TestCase):
    def test_clients_share_a_host_and_reconnect_to_the_same_project(self):
        with TemporaryDirectory() as location:
            workspace = Path(location) / "video projects"
            clients = [headless.StudioClient(workspace) for _ in range(2)]
            try:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    endpoints = list(pool.map(lambda client: client.base_url, clients))
                self.assertEqual(endpoints[0], endpoints[1])
                created = clients[0].request("POST", "/api/projects", data={"title": "Shared project"})
                project = clients[1].request("GET", f"/api/projects/{created['id']}")
                self.assertEqual(project["title"], "Shared project")
                self.assertIsNone(project["autoRun"])
                port, _ = running_instance(workspace)
                with self.assertRaisesRegex(RuntimeError, "instance changed"):
                    clients[1]._request((port, "stale instance"), "POST", "/api/projects", data={"title": "Stale"})
                self.assertEqual(len(clients[1].request("GET", "/api/projects")["projects"]), 1)
            finally:
                clients[0].stop()
                for client in clients:
                    if client.host:
                        client.host.wait(timeout=15)
            self.assertIsNone(running_instance(workspace))
            with mock.patch.object(headless, "_launch_host", side_effect=AssertionError("unexpected start")):
                clients[1].stop()

    def test_workspace_refuses_checkout_before_starting_a_host(self):
        with TemporaryDirectory() as location:
            root = Path(location)
            (root / ".git").mkdir()
            client = headless.StudioClient(root / "videos")
            with mock.patch.object(headless, "_launch_host", side_effect=AssertionError("unexpected start")):
                with self.assertRaisesRegex(ValueError, "Git checkout"):
                    client.request("GET", "/api/projects")
            self.assertEqual(list(root.iterdir()), [root / ".git"])
