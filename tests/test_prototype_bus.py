import json
import pathlib
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from bus.prototype import Bus, CASE, Handler


class SyntheticBusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(pathlib.Path(self.temp.name) / "mail.sqlite")
        self.bus = Bus(self.path, "a" * 32, deadline=2,
                       server_passwords={"doctor-a": "a_pw", "doctor-b": "b_pw"})
        self.bus.register("doctor-a", "http://127.0.0.1:41001", "ses_a",
                          "anthropic/claude-opus-5-5", "a" * 40)
        self.bus.register("doctor-b", "http://127.0.0.1:41002", "ses_b",
                          "openai/gpt-6.1-sol", "b" * 40)
        self.bus.set_phase("discussion")

    def tearDown(self):
        self.bus.db.close()
        self.temp.cleanup()

    def test_sender_bound_to_credential_and_session_with_dedupe(self):
        with self.assertRaises(ValueError):
            self.bus.send("doctor-a", "ses_unknown", "doctor-b", "hello", "k1")
        with self.assertRaises(ValueError):
            self.bus.send("doctor-a", "ses_a", "doctor-a", "hello", "k1")
        first = self.bus.send("doctor-a", "ses_a", "doctor-b", "hello", "k1")
        second = self.bus.send("doctor-a", "ses_a", "doctor-b", "hello", "k1")
        self.assertEqual(first["mail_id"], second["mail_id"])
        self.assertTrue(second["duplicate"])
        with self.assertRaises(ValueError):
            self.bus.send("doctor-a", "ses_a", "doctor-b", "different", "k1")

    def test_dispatch_completes_only_after_final_turn(self):
        self.bus.send("doctor-a", "ses_a", "doctor-b", "reply please", "k2")
        messages = []
        status = {}

        def fake_request(doctor, path, method="GET", body=None):
            if path == "/global/health": return 200, {"healthy": True}
            if path == "/session/status": return 200, status
            if path.endswith("/prompt_async"):
                messages.append({"info": {"role": "user", "id": "msg_user"},
                                 "parts": [{"type": "text", "text": body["parts"][0]["text"]}]})
                status["ses_b"] = {"type": "busy"}
                return 204, None
            if path.endswith("/message"): return 200, messages
            raise AssertionError(path)

        self.bus.request = fake_request
        self.bus.tick()
        self.assertEqual(self.bus.list_mail()[0]["state"], "dispatched")
        self.bus.tick()
        self.assertEqual(self.bus.list_mail()[0]["state"], "processing")
        status.clear()
        messages.append({"info": {"role": "assistant", "parentID": "msg_user",
                                   "time": {"completed": int(time.time()*1000)}, "finish": "tool-calls"}})
        self.bus.tick()
        self.assertEqual(self.bus.list_mail()[0]["state"], "processing")
        messages.append({"info": {"role": "assistant", "parentID": "msg_user",
                                   "time": {"completed": int(time.time()*1000)}, "finish": "stop"}})
        self.bus.tick()
        self.assertEqual(self.bus.list_mail()[0]["state"], "completed")

    def test_restart_reconciles_without_resending(self):
        mid = self.bus.send("doctor-a", "ses_a", "doctor-b", "hello", "k3")["mail_id"]
        with self.bus.lock:
            self.bus.db.execute("UPDATE mail SET state='dispatched', dispatched=? WHERE id=?", (time.time(), mid))
            self.bus.db.commit()
        restarted = Bus(self.path, "a" * 32, server_passwords=self.bus.server_passwords)
        try:
            self.assertEqual(restarted.list_mail()[0]["state"], "dispatched")
            marker = f"MAIL-ID: {mid}"
            messages = [
                {"info": {"role": "user", "id": "msg_1"}, "parts": [{"type": "text", "text": marker}]},
                {"info": {"role": "assistant", "parentID": "msg_1", "finish": "stop",
                          "time": {"completed": int(time.time()*1000)}}},
            ]

            def fake_request(doctor, path, method="GET", body=None):
                if path == "/global/health": return 200, {"healthy": True}
                if path == "/session/status": return 200, {}
                if path.endswith("/message"): return 200, messages
                raise AssertionError(path)

            restarted.request = fake_request
            restarted.tick()
            self.assertEqual(restarted.list_mail()[0]["state"], "completed")
        finally:
            restarted.db.close()

    def test_pause_uncertain_and_explicit_resolution(self):
        first = self.bus.send("doctor-a", "ses_a", "doctor-b", "one", "one")["mail_id"]
        self.bus.send("doctor-a", "ses_a", "doctor-b", "two", "two")
        delivered = []

        def fake_request(doctor, path, method="GET", body=None):
            if path == "/global/health": return 200, {"healthy": True}
            if path == "/session/status": return 200, {}
            if path.endswith("/message"): return 200, []
            if path.endswith("/prompt_async"):
                delivered.append(body)
                return 204, None
            raise AssertionError(path)

        self.bus.request = fake_request
        self.assertTrue(self.bus.set_paused("doctor-b", True)["safe_for_human_turn"])
        self.bus.tick()
        self.assertFalse(delivered)
        self.bus.set_paused("doctor-b", False)
        self.bus.tick()
        self.assertEqual(len(delivered), 1)
        self.assertFalse(self.bus.set_paused("doctor-b", True)["safe_for_human_turn"])
        with self.bus.lock:
            self.bus.db.execute("UPDATE mail SET dispatched=? WHERE id=?", (time.time()-10, first))
            self.bus.db.commit()
        self.bus.tick()
        self.assertEqual(self.bus.list_mail()[0]["state"], "uncertain")
        self.bus.set_paused("doctor-b", False)
        self.bus.tick()
        self.assertEqual(len(delivered), 1)
        self.assertEqual(self.bus.resolve(first, "cancel")["state"], "cancelled")
        self.bus.tick()
        self.assertEqual(len(delivered), 2)

    def test_phase_and_case_scope(self):
        self.assertEqual(CASE, "SYNTH-001")
        self.bus.set_phase("closed")
        with self.assertRaisesRegex(ValueError, "outside discussion"):
            self.bus.send("doctor-a", "ses_a", "doctor-b", "hello", "k4")

    def test_separate_synthetic_case_cannot_reuse_another_case_mail_db(self):
        from pathlib import Path
        other = Bus(str(Path(self.temp.name) / "other.sqlite"), "c" * 32,
                    server_passwords=self.bus.server_passwords, case_id="SYNTH-002")
        try:
            other.register("doctor-a", "http://127.0.0.1:41003", "ses_other_a",
                           "anthropic/claude-opus-5-5", "c" * 40)
            other.register("doctor-b", "http://127.0.0.1:41004", "ses_other_b",
                           "openai/gpt-6.1-sol", "d" * 40)
            other.set_phase("discussion")
            with self.assertRaisesRegex(ValueError, "case ID does not match"):
                other.send("doctor-a", "ses_other_a", "doctor-b", "wrong", "k",
                           case_id="SYNTH-001")
            self.assertEqual(other.send("doctor-a", "ses_other_a", "doctor-b", "correct", "k",
                                        case_id="SYNTH-002")["state"], "queued")
            with self.assertRaisesRegex(ValueError, "belongs to another case"):
                Bus(self.path, "c" * 32, server_passwords=self.bus.server_passwords,
                    case_id="SYNTH-002")
        finally:
            other.db.close()

    def test_http_role_and_admin_separation(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.bus = self.bus
        server.bus.running = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}"

        def request(path, token, body=None):
            req = urllib.request.Request(url + path,
                headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
                data=json.dumps(body).encode() if body is not None else None,
                method="POST" if body is not None else "GET")
            try:
                with urllib.request.urlopen(req, timeout=3) as response:
                    return response.status
            except urllib.error.HTTPError as exc:
                return exc.code

        try:
            letter = {"case_id": CASE, "sender_session": "ses_a", "recipient": "doctor-b",
                      "body": "synthetic", "dedupe_key": "http1"}
            self.assertEqual(request("/mail", "unknown-token", letter), 401)
            self.assertEqual(request("/mail", "a" * 40, letter), 200)
            self.assertEqual(request("/mail", "b" * 40, letter), 400)  # forged session
            self.assertEqual(request("/mail", "a" * 32, letter), 403)  # admin cannot impersonate doctor
            self.assertEqual(request("/phase", "a" * 40, {"phase": "closed"}), 403)
            self.assertEqual(request("/register", "a" * 40, {"role": "doctor-a"}), 403)
            self.assertEqual(request("/mail", "b" * 40,
                                     {**letter, "sender_session": "ses_b", "case_id": "GG-001"}), 400)
            self.assertEqual(request("/mail", "a" * 40), 403)
            self.assertEqual(request("/mail", "a" * 32), 200)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
