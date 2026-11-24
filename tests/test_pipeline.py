import os, sys, tempfile, unittest, subprocess, json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
TMP = tempfile.mkdtemp()
os.environ["CLIPFORGE_DATA"] = TMP
import numpy as np
from app import db, jobs, media, twitch
from app.pipeline import analyzer, clip, subtitles
from tests.test_detection import synth_wav, BURSTS, DUR

def ff(*a): subprocess.run(["ffmpeg", "-v", "error", "-y", *a], check=True)

class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init(); db.set_settings({"auto_previews": False})
        wav = os.path.join(TMP, "a.wav"); synth_wav(wav)
        cls.vid = os.path.join(TMP, "vod.mp4")
        ff("-f", "lavfi", "-i", f"testsrc2=s=640x360:r=15:d={DUR}", "-i", wav, "-c:v", "libx264", "-preset", "ultrafast",
           "-c:a", "aac", "-shortest", cls.vid)
        cls.vod = twitch.add_local(cls.vid, "Test VOD")

    def test_1_analyze(self):
        jid = jobs.enqueue("analyze", vod_id=self.vod["id"]); jobs.run_one()
        self.assertEqual(db.one("SELECT status FROM jobs WHERE id=?", (jid,))["status"], "done")
        ms = db.q("SELECT * FROM moments WHERE vod_id=? ORDER BY score DESC", (self.vod["id"],))
        self.assertGreaterEqual(len(ms), 3)
        top3 = sorted(ms[:3], key=lambda m: m["peak"])
        for m, b in zip(top3, BURSTS):
            self.assertLess(abs(m["peak"] - (b + 1.5)), 6)
            self.assertTrue(15 <= m["end"] - m["start"] <= 60)
        self.assertTrue(all(0 <= m["score"] <= 100 for m in ms))
        self.assertEqual(db.one("SELECT status FROM vods WHERE id=?", (self.vod["id"],))["status"], "analyzed")

    def _render(self, fmt, **opts):
        m = db.one("SELECT * FROM moments WHERE vod_id=? ORDER BY score DESC LIMIT 1", (self.vod["id"],))
        cid = db.run("INSERT INTO clips(moment_id,vod_id,fmt,options,created) VALUES(?,?,?,?,0)",
                     (m["id"], self.vod["id"], fmt, json.dumps(opts)))
        jobs.enqueue("render", clip_id=cid); jobs.run_one()
        c = db.one("SELECT * FROM clips WHERE id=?", (cid,))
        self.assertEqual(c["status"], "done", c)
        w, h = media.probe(c["path"])
        return c, (w, h)

    def test_2_formats(self):
        for fmt, size in (("9:16", (1080, 1920)), ("16:9", (1920, 1080)), ("1:1", (1080, 1080))):
            c, wh = self._render(fmt, subtitles=False)
            self.assertEqual(wh, size, fmt); self.assertGreater(os.path.getsize(c["path"]), 10_000)

    def test_3_webcam_rect_and_custom_trim(self):
        c, wh = self._render("9:16", subtitles=False, webcam_rect={"x": 0, "y": 0, "w": 160, "h": 90}, start=100, end=120, crop_x=0.3)
        self.assertEqual(wh, (1080, 1920)); self.assertAlmostEqual(c["duration"], 20, delta=0.1)
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", c["path"]],
                             capture_output=True, text=True).stdout
        self.assertAlmostEqual(float(out), 20, delta=0.6)

    def test_4_subtitles_burned(self):
        m = db.one("SELECT * FROM moments WHERE vod_id=? ORDER BY score DESC LIMIT 1", (self.vod["id"],))
        words = [{"w": w, "s": m["start"] + 1 + i * 0.4, "e": m["start"] + 1.35 + i * 0.4} for i, w in enumerate("oh mon dieu c'est pas vrai on a gagné !".split())]
        for style in subtitles.STYLES:
            out = os.path.join(TMP, f"sub_{style}.mp4")
            clip.render_file(self.vid, 0, m["start"], m["start"] + 8, out, "9:16", {"subtitle_style": style}, words, draft=True)
            self.assertGreater(os.path.getsize(out), 5000)

    def test_5_webcam_file_overlay_and_audio_mix(self):
        cam = os.path.join(TMP, "cam.mp4")
        ff("-f", "lavfi", "-i", "color=c=red:s=320x180:r=15:d=30", "-c:v", "libx264", "-preset", "ultrafast", cam)
        multi = os.path.join(TMP, "multi.mkv")
        ff("-i", self.vid, "-f", "lavfi", "-i", "sine=f=440:d=30", "-map", "0:v", "-map", "0:a", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-t", "30", multi)
        out = os.path.join(TMP, "mix.mp4")
        clip.render_file(multi, 0, 5, 15, out, "16:9", {"webcam_file": cam, "audio_tracks": [0, 1], "subtitles": False}, None, draft=True)
        self.assertGreater(os.path.getsize(out), 5000)

    def test_5b_clip_content_is_the_right_segment(self):
        """Le clip [90,110] doit contenir le pic audio injecté à 95 s (soit ~5 s dans le clip)."""
        from app.pipeline import audio
        out = os.path.join(TMP, "seg.mp4")
        clip.render_file(self.vid, 0, 90, 110, out, "16:9", {"subtitles": False}, None, draft=True)
        rms, _ = audio.profile(out, 20)
        self.assertTrue(4 <= int(np.argmax(rms)) <= 8, int(np.argmax(rms)))

    def test_5c_render_job_uses_correct_offset(self):
        m = db.one("SELECT * FROM moments WHERE vod_id=? ORDER BY score DESC LIMIT 1", (self.vod["id"],))
        from app.pipeline import audio
        c, _ = self._render("16:9", subtitles=False)
        rms, _ = audio.profile(c["path"], c["duration"])
        self.assertAlmostEqual(int(np.argmax(rms)) + m["start"], m["peak"], delta=6)

    def test_6_ass_content(self):
        words = [{"w": "Salut", "s": 10.0, "e": 10.4}, {"w": "{x}\\toi!", "s": 10.4, "e": 10.9}]
        ass = subtitles.build_ass(words, 1080, 1920, "karaoke", offset=10)
        self.assertIn("PlayResX: 1080", ass); self.assertIn("\\k40", ass); self.assertNotIn("{x}", ass)

    def test_7_settings_and_tokens(self):
        from app import secure
        self.assertEqual(db.get_settings()["clip_max"], 60)
        db.set_settings({"clip_max": 45, "bogus": 1}); s = db.get_settings()
        self.assertEqual(s["clip_max"], 45); self.assertNotIn("bogus", s)
        secure.save_account("twitch", {"token": {"access_token": "SECRET123"}})
        raw = db.one("SELECT data FROM accounts WHERE provider='twitch'")["data"]
        self.assertNotIn("SECRET123", raw); self.assertEqual(secure.load_account("twitch")["token"]["access_token"], "SECRET123")

    def test_8_duration_parse(self):
        self.assertEqual(twitch.parse_duration("3h2m1s"), 10921); self.assertEqual(twitch.parse_duration("45s"), 45)

    """Après l'analyse : aperçus 9:16 automatiques + galerie + zone webcam en fractions."""
    def test_9_auto_previews_and_gallery(self):
        from app import gallery
        from app.pipeline import clip as clipmod
        db.set_settings({"auto_previews": True, "max_moments": 3, "webcam_rect": {"x": 0.0, "y": 0.0, "w": 0.3, "h": 0.3}})
        db.update("vods", self.vod["id"], status="queued"); jobs.enqueue("analyze", vod_id=self.vod["id"])
        while jobs.run_one(): pass
        g = gallery.build("all")
        self.assertEqual(g["counts"]["all"], 3); self.assertEqual(g["counts"]["new"], 3)
        for m in g["items"]:
            self.assertEqual(m["preview"]["status"], "preview", m)
            c = db.one("SELECT * FROM clips WHERE id=?", (m["preview"]["id"],))
            self.assertEqual(media.probe(c["path"]), (540, 960)); self.assertTrue(os.path.getsize(os.path.splitext(c["path"])[0] + ".jpg") > 500)
        self.assertEqual(db.one("SELECT COUNT(*) n FROM clips WHERE draft=1")["n"], 3)
        # masquer / brouillon / régénération sans doublon
        mid = g["items"][0]["id"]; db.update("moments", mid, status="hidden")
        self.assertEqual(gallery.build("hidden")["counts"]["hidden"], 1); self.assertEqual(len(gallery.build("new")["items"]), 2)
        clipmod.queue_preview(mid); jobs.run_one()
        self.assertEqual(db.one("SELECT COUNT(*) n FROM clips WHERE draft=1 AND moment_id=?", (mid,))["n"], 1)
        # une ré-analyse remplace les aperçus (pas d'orphelins)
        db.update("vods", self.vod["id"], status="queued"); jobs.enqueue("analyze", vod_id=self.vod["id"])
        while jobs.run_one(): pass
        self.assertEqual(db.one("SELECT COUNT(*) n FROM clips WHERE draft=1")["n"], 3)
        db.set_settings({"auto_previews": False, "webcam_rect": None})

    def test_9b_norm_rect_and_frame(self):
        from app.pipeline.clip import norm_rect
        r = norm_rect({"x": 0.1, "y": 0.05, "w": 0.3, "h": 0.3}, 1920, 1080)
        self.assertEqual((r["x"], r["w"]), (192, 576)); self.assertTrue(all(v % 2 == 0 for v in r.values()))
        self.assertEqual(norm_rect({"x": 0, "y": 0, "w": 160, "h": 90}, 640, 360), {"x": 0, "y": 0, "w": 160, "h": 90})
        self.assertIsNone(norm_rect(None, 100, 100))
        self.assertTrue(media.frame_jpeg(self.vod, 30)[:3] == b"\xff\xd8\xff")

    def test_9c_layouts_and_saved_edit(self):
        from app.pipeline import clip as clipmod
        m = db.one("SELECT * FROM moments WHERE vod_id=? ORDER BY score DESC LIMIT 1", (self.vod["id"],))
        for lay in clipmod.LAYOUTS:
            out = os.path.join(TMP, f"lay_{lay}.mp4")
            clipmod.render_file(self.vid, 0, m["start"], m["start"] + 3, out, "9:16",
                                {"subtitles": False, "layout": lay, "webcam_rect": {"x": .02, "y": .04, "w": .28, "h": .3},
                                 "game_rect": {"x": .35, "y": .1, "w": .5, "h": .8}}, None, draft=True)
            self.assertEqual(media.probe(out), (540, 960), lay)
        # l'édition enregistrée d'un moment est reprise par l'aperçu automatique
        db.update("moments", m["id"], edit=json.dumps({"layout": "circle_facecam", "subtitles": False}))
        cid = clipmod.queue_preview(m["id"]); jobs.run_one()
        c = db.one("SELECT * FROM clips WHERE id=?", (cid,))
        self.assertEqual(c["status"], "preview"); self.assertEqual(json.loads(c["options"])["layout"], "circle_facecam")

if __name__ == "__main__":
    unittest.main(verbosity=2)
