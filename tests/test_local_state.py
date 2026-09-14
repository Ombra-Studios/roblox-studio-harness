"""Starea locală (1.0): directorul de stare, token-urile locale (UI, dispozitiv, admin), config.json și adresa hub-ului.

Toate testele scriu doar în directoare temporare (HARNESS_TEST_TMP sau %TEMP%), niciodată în %LOCALAPPDATA% real.
"""

import hashlib
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import local_state  # noqa: E402

HEX64 = "0123456789abcdef" * 4


def temp_dir():
    return tempfile.TemporaryDirectory(dir=os.environ.get("HARNESS_TEST_TMP"))


class StateDirTests(unittest.TestCase):
    def test_posix_uses_xdg_state_home_or_local_state(self):
        self.assertEqual(local_state.state_dir({"XDG_STATE_HOME": "/var/lib/x"}, platform="posix"), Path("/var/lib/x") / "studio-harness")
        self.assertEqual(local_state.state_dir({"HOME": "/home/ana"}, platform="posix"), Path("/home/ana") / ".local" / "state" / "studio-harness")

    def test_windows_uses_localappdata(self):
        self.assertEqual(local_state.state_dir({"LOCALAPPDATA": r"C:\Users\ana\AppData\Local"}, platform="nt"),
                         Path(r"C:\Users\ana\AppData\Local") / "StudioHarness")

    def test_override_wins_on_any_platform(self):
        self.assertEqual(local_state.state_dir({"STUDIO_HARNESS_STATE_DIR": "/srv/hub"}, platform="posix"), Path("/srv/hub"))
        self.assertEqual(local_state.state_dir({"STUDIO_HARNESS_STATE_DIR": r"D:\hub"}, platform="nt"), Path(r"D:\hub"))


class LocalTokenTests(unittest.TestCase):
    def test_local_token_is_created_once_reused_and_readable(self):
        with temp_dir() as temp:
            directory = Path(temp) / "state"
            self.assertIsNone(local_state.read_local_token(directory))
            first = local_state.ensure_local_token(directory)
            self.assertRegex(first, r"^[A-Za-z0-9_\-]{43}$")
            self.assertEqual(local_state.ensure_local_token(directory), first)
            self.assertEqual((directory / "local-token").read_text(encoding="utf-8"), first)
            self.assertEqual(local_state.read_local_token(directory), first)

    def test_padded_valid_content_is_kept_and_invalid_content_is_replaced(self):
        with temp_dir() as temp:
            directory = Path(temp)
            (directory / "local-token").write_text("  abcdefghijklmnop0123\n", encoding="utf-8")
            self.assertEqual(local_state.ensure_local_token(directory), "abcdefghijklmnop0123")
            self.assertEqual(local_state.read_local_token(directory), "abcdefghijklmnop0123")
            for bad in ("scurt", "are spatiu in interior 123", "", "x" * 513):
                with self.subTest(bad=bad):
                    (directory / "local-token").write_text(bad, encoding="utf-8")
                    self.assertIsNone(local_state.read_local_token(directory))
                    fresh = local_state.ensure_local_token(directory)
                    self.assertRegex(fresh, r"^[A-Za-z0-9_\-]{43}$")
                    self.assertEqual(local_state.read_local_token(directory), fresh)

    def test_binary_garbage_is_replaced_instead_of_crashing(self):
        with temp_dir() as temp:
            directory = Path(temp)
            (directory / "local-token").write_bytes(b"\xff\xfe\x00garbage")
            self.assertIsNone(local_state.read_local_token(directory))
            self.assertRegex(local_state.ensure_local_token(directory), r"^[A-Za-z0-9_\-]{43}$")


class DeviceTokenTests(unittest.TestCase):
    def test_device_token_is_64_hex_created_once_in_device_token_file(self):
        with temp_dir() as temp:
            directory = Path(temp) / "StudioHarness"
            self.assertIsNone(local_state.read_device_token(directory))
            token = local_state.ensure_device_token(directory)
            self.assertRegex(token, r"^[0-9a-f]{64}$")
            self.assertEqual(local_state.ensure_device_token(directory), token)
            self.assertEqual((directory / "device-token").read_text(encoding="utf-8"), token)
            self.assertEqual(local_state.read_device_token(directory), token)
            # Token-ul de dispozitiv și codul UI sunt secrete diferite, în fișiere diferite.
            self.assertNotEqual(local_state.ensure_local_token(directory), token)
            self.assertEqual(sorted(path.name for path in directory.iterdir()), ["device-token", "local-token"])

    def test_two_directories_get_different_device_tokens(self):
        with temp_dir() as first, temp_dir() as second:
            self.assertNotEqual(local_state.ensure_device_token(Path(first)), local_state.ensure_device_token(Path(second)))

    def test_content_that_is_not_64_hex_is_regenerated_but_uppercase_hex_is_kept(self):
        with temp_dir() as temp:
            directory = Path(temp)
            for bad in ("", "abc", "g" * 64, "0" * 63, "0" * 65, "a" * 43):
                with self.subTest(bad=bad):
                    (directory / "device-token").write_text(bad, encoding="utf-8")
                    self.assertIsNone(local_state.read_device_token(directory))
                    self.assertRegex(local_state.ensure_device_token(directory), r"^[0-9a-f]{64}$")
            # Un token scris de mână cu majuscule rămâne același dispozitiv: hub-ul reține sha256 al textului exact.
            upper = HEX64.upper()
            (directory / "device-token").write_text(upper + "\n", encoding="utf-8")
            self.assertEqual(local_state.read_device_token(directory), upper)
            self.assertEqual(local_state.ensure_device_token(directory), upper)

    def test_device_id_is_the_first_16_hex_of_sha256(self):
        expected = hashlib.sha256(HEX64.encode("utf-8")).hexdigest()[:16]
        self.assertEqual(local_state.device_id_for(HEX64), expected)
        self.assertRegex(local_state.device_id_for(HEX64), r"^[0-9a-f]{16}$")
        self.assertNotEqual(local_state.device_id_for(HEX64), local_state.device_id_for(HEX64.upper()))
        self.assertEqual(local_state.DEVICE_ID_LENGTH, 16)


class AdminTokenTests(unittest.TestCase):
    def test_admin_token_lives_in_hub_admin_token_and_is_created_once(self):
        with temp_dir() as temp:
            directory = Path(temp) / "hub"
            first = local_state.ensure_admin_token(directory)
            self.assertRegex(first, r"^[A-Za-z0-9_\-]{43}$")
            self.assertEqual(local_state.ensure_admin_token(directory), first)
            self.assertEqual((directory / "hub-admin-token").read_text(encoding="utf-8"), first)
            self.assertFalse((directory / "team-token").exists())
            self.assertNotEqual(local_state.ensure_local_token(directory), first)


class TokenFileTests(unittest.TestCase):
    def test_creation_survives_a_concurrent_writer(self):
        with temp_dir() as temp:
            path = Path(temp) / "local-token"
            original_open = Path.open
            calls = []

            def racing_open(self, mode="r", *args, **kwargs):
                # Prima creare exclusivă pierde cursa: alt proces a scris între timp un token valid.
                if self == path and mode == "x" and not calls:
                    calls.append(mode)
                    with original_open(self, "w", encoding="utf-8") as stream:
                        stream.write("token-scris-de-altcineva-123")
                    raise FileExistsError(str(path))
                return original_open(self, mode, *args, **kwargs)

            with patch.object(Path, "open", racing_open):
                self.assertEqual(local_state.ensure_token_file(path), "token-scris-de-altcineva-123")
            self.assertEqual(calls, ["x"])

    def test_gives_up_after_three_attempts_with_the_file_name_in_the_error(self):
        with temp_dir() as temp:
            path = Path(temp) / "device-token"
            original_open = Path.open
            attempts = []

            def always_taken(self, mode="r", *args, **kwargs):
                # Fișierul apare mereu între citire (lipsă) și creare exclusivă; după trei încercări renunțăm.
                if mode == "x":
                    attempts.append(mode)
                    raise FileExistsError(str(path))
                return original_open(self, mode, *args, **kwargs)

            with patch.object(Path, "open", always_taken), self.assertRaisesRegex(RuntimeError, "device-token"):
                local_state.ensure_token_file(path, local_state.DEVICE_TOKEN_PATTERN, lambda: HEX64)
            self.assertEqual(attempts, ["x", "x", "x"])
            self.assertFalse(path.exists())

    def test_creates_missing_parent_directories(self):
        with temp_dir() as temp:
            path = Path(temp) / "a" / "b" / "local-token"
            token = local_state.ensure_token_file(path)
            self.assertEqual(path.read_text(encoding="utf-8"), token)

    @unittest.skipIf(os.name == "nt", "permisiunile POSIX nu există pe Windows")
    def test_posix_token_files_are_owner_only(self):
        with temp_dir() as temp:
            directory = Path(temp)
            local_state.ensure_local_token(directory)
            local_state.ensure_device_token(directory)
            local_state.ensure_admin_token(directory)
            for name in ("local-token", "device-token", "hub-admin-token"):
                self.assertEqual(stat.S_IMODE((directory / name).stat().st_mode), 0o600, name)

    def test_permissions_are_restricted_on_posix_only(self):
        with temp_dir() as temp, patch.object(local_state.os, "chmod") as chmod:
            directory = Path(temp)
            local_state.ensure_local_token(directory)
            local_state.ensure_device_token(directory)
            local_state.ensure_admin_token(directory)
            if os.name == "nt":
                chmod.assert_not_called()
            else:
                self.assertEqual([call.args for call in chmod.call_args_list],
                                 [(directory / "local-token", 0o600), (directory / "device-token", 0o600), (directory / "hub-admin-token", 0o600)])


class ConfigTests(unittest.TestCase):
    def test_missing_invalid_or_non_object_config_is_empty(self):
        with temp_dir() as temp:
            directory = Path(temp)
            self.assertEqual(local_state.read_config(directory), {})
            for content in ("{", "[1, 2]", '"text"', "null", ""):
                with self.subTest(content=content):
                    (directory / "config.json").write_text(content, encoding="utf-8")
                    self.assertEqual(local_state.read_config(directory), {})
            (directory / "config.json").write_bytes(b"\xff\xfe")
            self.assertEqual(local_state.read_config(directory), {})

    def test_object_config_is_returned_as_is(self):
        with temp_dir() as temp:
            directory = Path(temp)
            (directory / "config.json").write_text(json.dumps({"hub_url": "https://hub.exemplu.ro/", "altceva": 1}), encoding="utf-8")
            self.assertEqual(local_state.read_config(directory), {"hub_url": "https://hub.exemplu.ro/", "altceva": 1})


class HubUrlTests(unittest.TestCase):
    def write_config(self, directory, value):
        (directory / "config.json").write_text(json.dumps({"hub_url": value}), encoding="utf-8")

    def test_default_is_the_hosted_hub_without_trailing_slash(self):
        self.assertEqual(local_state.DEFAULT_HUB_URL, "https://lostcube.pro/roblox/harness")
        self.assertEqual(local_state.HUB_URL_ENV, "STUDIO_HARNESS_HUB_URL")
        with temp_dir() as temp:
            self.assertEqual(local_state.hub_url(Path(temp), {}), "https://lostcube.pro/roblox/harness")
            self.assertEqual(local_state.resolve_hub_url(Path(temp), {}), ("https://lostcube.pro/roblox/harness", "default"))
            # Un director de stare inexistent este tot „fără config”.
            self.assertEqual(local_state.hub_url(Path(temp) / "lipsa", {}), "https://lostcube.pro/roblox/harness")

    def test_env_beats_config_which_beats_default(self):
        with temp_dir() as temp:
            directory = Path(temp)
            self.write_config(directory, "https://hub.echipa.ro/harness/")
            self.assertEqual(local_state.resolve_hub_url(directory, {}), ("https://hub.echipa.ro/harness", "config"))
            env = {"STUDIO_HARNESS_HUB_URL": "http://127.0.0.1:45123/"}
            self.assertEqual(local_state.resolve_hub_url(directory, env), ("http://127.0.0.1:45123", "env"))
            self.assertEqual(local_state.hub_url(directory, env), "http://127.0.0.1:45123")

    def test_empty_env_or_config_falls_through_to_the_next_source(self):
        with temp_dir() as temp:
            directory = Path(temp)
            self.assertEqual(local_state.resolve_hub_url(directory, {"STUDIO_HARNESS_HUB_URL": ""}), (local_state.DEFAULT_HUB_URL, "default"))
            self.assertEqual(local_state.resolve_hub_url(directory, {"STUDIO_HARNESS_HUB_URL": "   "}), (local_state.DEFAULT_HUB_URL, "default"))
            for empty in ("", "  ", None):
                with self.subTest(empty=empty):
                    self.write_config(directory, empty)
                    self.assertEqual(local_state.resolve_hub_url(directory, {}), (local_state.DEFAULT_HUB_URL, "default"))
            (directory / "config.json").write_text("{}", encoding="utf-8")
            self.write_config(directory, "https://hub.echipa.ro")
            self.assertEqual(local_state.resolve_hub_url(directory, {"STUDIO_HARNESS_HUB_URL": ""}), ("https://hub.echipa.ro", "config"))

    def test_a_configured_but_invalid_value_disables_the_hub_instead_of_using_the_public_default(self):
        with temp_dir() as temp:
            directory = Path(temp)
            self.write_config(directory, "https://hub.echipa.ro")
            # Env-ul invalid nu cade pe config.json și nici pe implicit.
            self.assertEqual(local_state.resolve_hub_url(directory, {"STUDIO_HARNESS_HUB_URL": "ftp://hub"}), (None, "env"))
            self.assertIsNone(local_state.hub_url(directory, {"STUDIO_HARNESS_HUB_URL": "http://192.168.1.10:34880"}))
            for bad in ("ftp://hub", "http://hub.echipa.ro", "hub.echipa.ro", 5, True, ["https://hub"], {"url": "https://hub"}):
                with self.subTest(bad=bad):
                    self.write_config(directory, bad)
                    self.assertEqual(local_state.resolve_hub_url(directory, {}), (None, "config"))

    def test_normalize_accepts_https_anywhere_and_http_only_on_loopback(self):
        normalize = local_state.normalize_hub_url
        self.assertEqual(normalize("https://lostcube.pro/roblox/harness/"), "https://lostcube.pro/roblox/harness")
        self.assertEqual(normalize("https://lostcube.pro/roblox/harness///"), "https://lostcube.pro/roblox/harness")
        self.assertEqual(normalize("  https://hub.echipa.ro/  "), "https://hub.echipa.ro")
        self.assertEqual(normalize("https://hub.echipa.ro:8443/x"), "https://hub.echipa.ro:8443/x")
        self.assertEqual(normalize("HTTPS://Hub.Echipa.ro/"), "https://Hub.Echipa.ro")
        self.assertEqual(normalize("http://127.0.0.1:34880"), "http://127.0.0.1:34880")
        self.assertEqual(normalize("http://localhost:40001/roblox/harness/"), "http://localhost:40001/roblox/harness")
        self.assertEqual(normalize("http://LOCALHOST/"), "http://LOCALHOST")
        self.assertEqual(normalize("http://127.0.0.1"), "http://127.0.0.1")
        for bad in ("http://hub.echipa.ro", "http://192.168.1.10:34880", "http://127.0.0.1.evil.ro", "http://localhost.evil.ro", "http://[::1]:34880",
                    "ftp://hub", "hub.echipa.ro", "//hub.echipa.ro", "https://", "https:///cale", "https://ana:parola@hub.echipa.ro",
                    "https://ana@hub.echipa.ro", "https://hub.echipa.ro/?x=1", "https://hub.echipa.ro/#frag", "https://hub.echipa.ro/a b",
                    "https://hub.echipa.ro/\tx", "https://hub.echipa.ro:abc", "https://hub.echipa.ro:0", "https://hub.echipa.ro:70000", "", "   ",
                    "https://" + "a" * 600, None, 7, 1.5, b"https://hub", ["https://hub"]):
            with self.subTest(bad=bad):
                self.assertIsNone(normalize(bad))

    def test_process_environment_is_used_when_no_environ_is_given(self):
        with temp_dir() as temp, patch.dict(os.environ, {"STUDIO_HARNESS_HUB_URL": "http://localhost:40002/"}):
            self.assertEqual(local_state.hub_url(Path(temp)), "http://localhost:40002")
        with temp_dir() as temp, patch.dict(os.environ, {}, clear=False):
            os.environ.pop("STUDIO_HARNESS_HUB_URL", None)
            self.assertEqual(local_state.hub_url(Path(temp)), local_state.DEFAULT_HUB_URL)


class RemovedTeamApiTests(unittest.TestCase):
    def test_team_helpers_are_gone_in_1_0(self):
        for name in ("ensure_team_token", "read_team", "developer_name", "NAME_PATTERN"):
            self.assertFalse(hasattr(local_state, name), name)

    def test_team_json_is_never_read_for_the_hub_url(self):
        with temp_dir() as temp:
            directory = Path(temp)
            (directory / "team.json").write_text(json.dumps({"developer": "ana", "hub_url": "https://hub.veche.ro", "team_token": "x" * 32}), encoding="utf-8")
            self.assertEqual(local_state.hub_url(directory, {}), local_state.DEFAULT_HUB_URL)
            self.assertEqual(local_state.read_config(directory), {})


if __name__ == "__main__":
    unittest.main()
