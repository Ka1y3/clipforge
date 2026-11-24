import os, sys, tempfile, unittest, wave, subprocess
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from app.pipeline import audio, scoring, video

SR = 16000
BURSTS = [95, 260, 410]          # secondes des pics injectés
DUR = 480

def synth_wav(path):
    rng = np.random.default_rng(1)
    t = np.arange(DUR * SR) / SR
    # "voix" de fond : bruit filtré à niveau variable lentement
    env = 0.03 + 0.015 * np.sin(2 * np.pi * t / 37)
    x = rng.normal(0, 1, len(t)) * env
    for b in BURSTS:                        # cris : forte énergie 2–4 kHz, montée brutale
        m = (t >= b) & (t < b + 3)
        tone = np.sin(2 * np.pi * 2800 * t[m]) + 0.6 * np.sin(2 * np.pi * 3400 * t[m])
        x[m] += 0.5 * tone * np.hanning(m.sum()) ** 0.3
    x = np.clip(x, -1, 1)
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((x * 32767).astype(np.int16).tobytes())

class Detection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(); cls.wav = os.path.join(cls.tmp, "a.wav"); synth_wav(cls.wav)
        cls.rms, cls.hf = audio.profile(cls.wav, DUR)

    def test_profile_length(self):
        self.assertAlmostEqual(len(self.rms), DUR, delta=2)

    def test_peaks_found(self):
        sig = scoring.audio_signal(self.rms, self.hf)
        fused = scoring.combine({"audio": sig}, {"audio": 1})
        peaks = scoring.pick_peaks(fused, 3, min_gap=45)
        found = sorted(p for p, _ in peaks)
        self.assertEqual(len(found), 3)
        for f, b in zip(found, BURSTS):
            self.assertLess(abs(f - (b + 1.5)), 6, f"pic {f} vs {b}")

    def test_no_false_positive_on_quiet(self):
        quiet = self.rms[:80]; hf = self.hf[:80]
        fused = scoring.combine({"audio": scoring.audio_signal(quiet, hf)}, {"audio": 1})
        self.assertEqual(scoring.pick_peaks(fused, 3, floor=0.5), [])

    def test_boundaries(self):
        s, e = scoring.snap_boundaries(96, self.rms, DUR)
        self.assertTrue(15 <= e - s <= 60); self.assertLess(s, 96); self.assertGreater(e, 96)
        s, e = scoring.snap_boundaries(2, self.rms, DUR)
        self.assertGreaterEqual(s, 0)
        s, e = scoring.snap_boundaries(DUR - 1, self.rms, DUR)
        self.assertLessEqual(e, DUR)

    def test_chat_signal_and_fusion(self):
        msgs = [(t, "msg") for t in range(0, 300, 6)] + [(t + i * 0.1, "KEKW") for t in (150,) for i in range(60)]
        cs = scoring.chat_signal(msgs, 300)
        self.assertGreater(cs[150:160].max(), 0.6); self.assertLess(cs[20], 0.4)
        f = scoring.combine({"audio": None, "chat": cs}, {"audio": .5, "chat": .3})
        self.assertEqual(int(np.argmax(f)) // 10, 15)

    def test_text_score(self):
        self.assertGreater(scoring.text_score("Oh mon dieu hahaha non non c'est pas vrai !!"), 0.5)
        self.assertEqual(scoring.text_score(""), 0.0)
        self.assertLess(scoring.text_score("bon on va y aller"), 0.2)

    def test_final_score_bounds(self):
        self.assertTrue(0 <= scoring.final_score(1, 1, 1, 10) <= 100)
        self.assertGreater(scoring.final_score(0.8, 0.5, 0.5, 9), scoring.final_score(0.8, 0.5, 0.5, 2))

    def test_video_motion(self):
        v = os.path.join(self.tmp, "v.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=6",
                        "-pix_fmt", "yuv420p", v], check=True)
        m, cx = video.motion_profile(v)
        self.assertGreaterEqual(len(m), 5)
        self.assertTrue(0 <= video.smart_crop_x(v) <= 1)

if __name__ == "__main__":
    unittest.main(verbosity=2)
