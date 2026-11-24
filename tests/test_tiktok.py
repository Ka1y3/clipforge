import os, sys, tempfile, unittest, json, hashlib, urllib.parse
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
TMP = tempfile.mkdtemp(); os.environ["CLIPFORGE_DATA"] = TMP
os.environ.update(TIKTOK_CLIENT_KEY="ck", TIKTOK_CLIENT_SECRET="cs")
from app import db, secure
from app.publish import tiktok

class Fake:
    def __init__(self, chunks_ok=True): self.calls = []; self.polls = 0
    def request(self, method, url, params=None, data=None, headers=None, json_body=None, timeout=60, raw=False):
        self.calls.append((method, url, json_body, data if not isinstance(data, bytes) else len(data), headers))
        if url.endswith("/oauth/token/"): return {"access_token": "act.x", "refresh_token": "r", "expires_in": 86400}
        if url.endswith("creator_info/query/"): return {"data": {"creator_username": "aliya", "privacy_level_options": ["SELF_ONLY"], "max_video_post_duration_sec": 600}, "error": {"code": "ok"}}
        if url.endswith("video/init/") or url.endswith("inbox/video/init/"): return {"data": {"publish_id": "v_pub~1", "upload_url": "https://open-upload.tiktokapis.com/upload/?x=1"}, "error": {"code": "ok"}}
        if method == "PUT": return (206 if "bytes 0-" in headers["Content-Range"] and not headers["Content-Range"].endswith(f"/{self.size}") else 201, {}, b"")
        if url.endswith("status/fetch/"):
            self.polls += 1
            return {"data": {"status": "PROCESSING_UPLOAD" if self.polls < 2 else self.final}, "error": {"code": "ok"}}
        raise AssertionError(url)

class T(unittest.TestCase):
    def setUp(self):
        db.init(); self.f = Fake(); tiktok.H.request = self.f.request; tiktok.time.sleep = lambda s: None
        self.p = os.path.join(TMP, "c.mp4"); open(self.p, "wb").write(b"x" * 1000); self.f.size = 1000; self.f.final = "PUBLISH_COMPLETE"
        secure.save_account("tiktok", {"token": {"access_token": "act.x", "refresh_token": "r", "expires_at": 9e12}})

    def test_pkce_and_url(self):
        v, c = tiktok.pkce_pair(); self.assertEqual(c, hashlib.sha256(v.encode()).hexdigest()); self.assertTrue(43 <= len(v) <= 128)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(tiktok.login_url()).query)
        self.assertEqual(q["code_challenge_method"], ["S256"]); self.assertEqual(q["client_key"], ["ck"]); self.assertIn("video.publish", q["scope"][0])

    def test_callback_rejects_bad_state(self):
        with self.assertRaises(ValueError): tiktok.handle_callback("code", "nope")

    def test_chunk_plan(self):
        self.assertEqual(tiktok.plan_chunks(1000), (1000, 1))
        c, n = tiktok.plan_chunks(50_000_123); self.assertEqual((c, n), (tiktok.CHUNK, 1))
        c, n = tiktok.plan_chunks(100 * 1024 * 1024); self.assertEqual(n, 3); self.assertGreaterEqual(100 * 1024 * 1024 - (n - 1) * c, 5 * 1024 * 1024)

    def test_direct_post_flow(self):
        pid, url, note = tiktok.publish({"path": self.p, "duration": 30}, "Titre", "", "#a #b", {"mode": "direct", "privacy": "SELF_ONLY"})
        self.assertEqual(pid, "v_pub~1"); self.assertIn("privé", note)
        init = [c for c in self.f.calls if c[1].endswith("/video/init/")][0][2]
        self.assertEqual(init["post_info"]["title"], "Titre #a #b"); self.assertEqual(init["source_info"]["total_chunk_count"], 1)
        put = [c for c in self.f.calls if c[0] == "PUT"][0]; self.assertEqual(put[4]["Content-Range"], "bytes 0-999/1000")

    def test_refuses_disallowed_privacy_and_too_long(self):
        with self.assertRaises(RuntimeError): tiktok.publish({"path": self.p, "duration": 30}, "t", "", "", {"privacy": "PUBLIC_TO_EVERYONE"})
        with self.assertRaises(RuntimeError): tiktok.publish({"path": self.p, "duration": 9999}, "t", "", "", {"privacy": "SELF_ONLY"})

    def test_draft_mode_and_failure(self):
        self.f.final = "SEND_TO_USER_INBOX"
        pid, url, note = tiktok.publish({"path": self.p, "duration": 30}, "t", "", "", {"mode": "draft"})
        self.assertTrue(any(c[1].endswith("inbox/video/init/") for c in self.f.calls)); self.assertIn("boîte", note)
        self.f.polls = 0; self.f.final = "FAILED"
        with self.assertRaises(RuntimeError): tiktok.publish({"path": self.p, "duration": 30}, "t", "", "", {"mode": "draft"})

if __name__ == "__main__": unittest.main()
