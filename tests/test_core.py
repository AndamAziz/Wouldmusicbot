import os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ytdl, store, bot


class Ytdl(unittest.TestCase):
    def test_video_id(self):
        for u in ("https://www.youtube.com/watch?v=jeOMxi88Tf0&t=3", "https://youtu.be/jeOMxi88Tf0",
                  "https://youtube.com/shorts/jeOMxi88Tf0", "https://music.youtube.com/watch?v=jeOMxi88Tf0&list=x"):
            self.assertEqual(ytdl.video_id(u), "jeOMxi88Tf0")
        self.assertIsNone(ytdl.video_id("zakaria abdulla"))

    def test_split_title_and_duration(self):
        self.assertEqual(ytdl._split_title("Zakaria - Netbinm Rozhe (Official Video)", "AriJ87"), ("Zakaria", "Netbinm Rozhe"))
        self.assertEqual(ytdl._split_title("Halparke", "Rebin - Topic"), ("Rebin", "Halparke"))
        self.assertEqual(ytdl.fmt_duration(340), "5:40")
        self.assertEqual(ytdl.fmt_duration(3725), "1:02:05")


class Keyboards(unittest.TestCase):
    def test_callback_data_fits_telegram_limit(self):
        items = [ytdl.Item("A" * 11, "x" * 200, 100, "c") for _ in range(8)]
        for kb in (bot.results_keyboard(items), bot.formats_keyboard("A" * 11)):
            for row in kb.inline_keyboard:
                for b in row:
                    if b.callback_data:
                        self.assertLessEqual(len(b.callback_data.encode()), 64)
                        self.assertTrue(b.callback_data.startswith(("p:", "d:")))
        kinds = {b.callback_data.split(":")[2] for row in bot.formats_keyboard("A" * 11).inline_keyboard
                 for b in row if b.callback_data}
        self.assertEqual(kinds, set(bot.KIND_LABEL))


class Store(unittest.TestCase):
    def test_cache_and_hourly_limit(self):
        s = store.Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        self.assertIsNone(s.cached("vid", "a320"))
        s.remember("vid", "a320", "FILEID")
        self.assertEqual(s.cached("vid", "a320"), "FILEID")
        self.assertIsNone(s.cached("vid", "v720"))
        s.forget("vid", "a320")
        self.assertIsNone(s.cached("vid", "a320"))
        for i in range(3):
            s.use(7, now=1000.0 + i)
        self.assertEqual(s.used_last_hour(7, now=1100.0), 3)
        self.assertEqual(s.used_last_hour(7, now=1000.0 + 3700), 0)
        self.assertEqual(s.used_last_hour(8, now=1100.0), 0)


if __name__ == "__main__":
    unittest.main()
