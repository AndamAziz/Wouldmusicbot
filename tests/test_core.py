import os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ytdl, store, bot


class Ytdl(unittest.TestCase):
    def test_video_id_and_urls(self):
        for u in ("https://www.youtube.com/watch?v=jeOMxi88Tf0&t=3", "https://youtu.be/jeOMxi88Tf0",
                  "https://youtube.com/shorts/jeOMxi88Tf0"):
            self.assertEqual(ytdl.video_id(u), "jeOMxi88Tf0")
        txt = "see https://vt.tiktok.com/ZS123/ and (https://www.instagram.com/reel/abc/), https://vt.tiktok.com/ZS123/"
        self.assertEqual(ytdl.urls_in(txt), ["https://vt.tiktok.com/ZS123/", "https://www.instagram.com/reel/abc/"])
        self.assertEqual(ytdl.urls_in("zakaria abdulla"), [])

    def test_titles_sizes_sites(self):
        self.assertEqual(ytdl._split_title("Zakaria - Netbinm Rozhe (Official Video)", "x"), ("Zakaria", "Netbinm Rozhe"))
        self.assertEqual(ytdl._split_title("Halparke", "Rebin - Topic"), ("Rebin", "Halparke"))
        self.assertEqual(ytdl.fmt_duration(3725), "1:02:05")
        self.assertEqual(ytdl.fmt_size(9_600_000), "9.2 MB")
        self.assertEqual(ytdl.site_label("TikTok"), "🎵 TikTok")
        self.assertEqual(ytdl.site_label("TwitchClips"), "🟣 Twitch")
        self.assertEqual(ytdl.site_label("Odysee"), "🌐 Odysee")

    def test_qualities_use_the_short_side(self):
        f = lambda w, h: {"width": w, "height": h, "vcodec": "h264"}
        self.assertEqual(ytdl.qualities([f(720, 1280), f(540, 960)]), [720, 480, 360])        # vertical TikTok
        self.assertEqual(ytdl.qualities([f(1920, 1080), f(1280, 720)]), [1080, 720, 480, 360])
        self.assertEqual(ytdl.qualities([{"vcodec": "none"}]), [])

    def test_format_opts(self):
        o, ext = ytdl.format_opts("a128")
        self.assertEqual((ext, o["postprocessors"][0]["preferredquality"]), ("mp3", "128"))
        o, ext = ytdl.format_opts("v720")
        self.assertEqual((ext, o["format_sort"][0]), ("mp4", "res:720"))
        self.assertEqual(ytdl.format_opts("vbest")[0]["format_sort"][0], "res:1080")

    def test_media_from_info(self):
        m = ytdl._media("u", {"title": "T", "extractor_key": "Soundcloud", "formats": [{"vcodec": "none"}]})
        self.assertFalse(m.has_video)
        m = ytdl._media("u", {"_type": "playlist", "entries": [{"title": "A", "extractor_key": "Instagram",
                                                                "formats": [{"width": 1080, "height": 1350, "vcodec": "h264"}]}, {"title": "B"}]})
        self.assertEqual((m.title, m.count, m.qualities[0], m.site), ("A", 2, 1080, "📸 Instagram"))


class Keyboards(unittest.TestCase):
    def all_data(self, kb):
        return [b.callback_data for row in kb.inline_keyboard for b in row if b.callback_data]

    def test_callback_data_fits_and_is_known(self):
        key, qkey = "a" * 12, "b" * 10
        items = [ytdl.Item("A" * 11, "x" * 200, 100, "c") for _ in range(16)]
        media = ytdl.Media("https://x.y/" + "z" * 300, "t", qualities=[1080, 720, 480, 360])
        kbs = [bot.results_kb(qkey, items, [key] * 16, 0), bot.results_kb(qkey, items, [key] * 16, 1),
               bot.card_kb(key, media), bot.after_kb(key, "a320", media.url, "v720"),
               bot.settings_kb(store.DEFAULTS)]
        for kb in kbs:
            for d in self.all_data(kb):
                self.assertLessEqual(len(d.encode()), 64, d)
                self.assertTrue(d in ("x", "noop") or d.split(":")[0] in ("s", "l", "d", "c", "set"), d)
        kinds = {d.split(":")[2] for d in self.all_data(bot.card_kb(key, media)) if d.startswith("d:")}
        self.assertEqual(kinds, {"a320", "a128", "v1080", "v720", "v480", "v360"})
        audio_only = ytdl.Media("u", "t", has_video=False)
        self.assertEqual({d.split(":")[2] for d in self.all_data(bot.card_kb(key, audio_only)) if d.startswith("d:")},
                         {"a320", "a128"})
        self.assertIn(f"d:{key}:vbest", self.all_data(bot.card_kb(key, ytdl.Media("u", "t"))))   # quality unknown

    def test_pages_and_video_pick(self):
        items = [ytdl.Item("A" * 11, "t", 1, "c") for _ in range(16)]
        self.assertEqual(sum(d.startswith("l:") for d in self.all_data(bot.results_kb("q", items, ["k"] * 16, 1))), 8)
        m = ytdl.Media("u", "t", qualities=[720, 480, 360])
        self.assertEqual(bot.pick_video_kind(m, "v1080"), "v720")
        self.assertEqual(bot.pick_video_kind(m, "v480"), "v480")
        self.assertEqual(bot.pick_video_kind(ytdl.Media("u", "t", qualities=[1080]), "v360"), "v1080")
        self.assertEqual(bot.pick_video_kind(ytdl.Media("u", "t"), "v720"), "vbest")
        self.assertEqual(bot.bar(42), "▰▰▰▰▱▱▱▱▱▱ 42%")


class Store(unittest.TestCase):
    def test_links_cache_settings_limits(self):
        s = store.Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        k = s.link("https://www.tiktok.com/@a/video/1", "T")
        self.assertEqual(len(k), 12)
        self.assertEqual(s.link("https://www.tiktok.com/@a/video/1"), k)
        self.assertEqual(s.url(k), ("https://www.tiktok.com/@a/video/1", "T"))   # title kept
        self.assertEqual(s.url("missing"), (None, None))
        s.remember(k, "a320", "FID")
        self.assertEqual((s.cached(k, "a320"), s.cached(k, "v720")), ("FID", None))
        s.forget(k, "a320")
        self.assertIsNone(s.cached(k, "a320"))
        self.assertEqual(s.settings(5), store.DEFAULTS)
        s.set(5, "mode", "audio")
        self.assertEqual(s.set(5, "vq", "v480"), {"mode": "audio", "aq": "a320", "vq": "v480"})
        self.assertEqual(s.settings(6), store.DEFAULTS)
        for i in range(3):
            s.use(7, now=1000.0 + i)
        self.assertEqual((s.used_last_hour(7, now=1100.0), s.used_last_hour(7, now=4700.0)), (3, 0))


if __name__ == "__main__":
    unittest.main()
