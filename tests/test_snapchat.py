"""Snapchat: page parsing and the bot flow (single snap, story album, next page) with fakes."""
import asyncio, json, os, sys, tempfile, types, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bot, config, snapchat, ytdl
from test_flow import FakeApp, Msg, data

SPOT = ('<script type="application/ld+json">{"@type":"BreadcrumbList","itemListElement":[{"name":"Shrey (@shrey57)"}]}'
        '</script><script type="application/ld+json">{"@type":"VideoObject","name":"Views","contentUrl":'
        '"https://cf-st.sc-cdn.net/d/abc.1034.IRZXSOY?mo=x","thumbnailUrl":"https://cf-st.sc-cdn.net/d/abc.256"}</script>')


def snap(i, t=1):
    return {"snapMediaType": t, "snapUrls": {"mediaUrl": f"https://cf-st.sc-cdn.net/d/s{i}.27.IRZXSOY",
                                             "mediaPreviewUrl": {"value": f"https://cf-st.sc-cdn.net/d/p{i}"}}}


PROFILE = '<script id="__NEXT_DATA__" type="application/json">' + json.dumps({"props": {"pageProps": {
    "userProfile": {"publicProfileInfo": {"username": "kylie", "title": "King Kylie", "profilePictureUrl": "https://pic"}},
    "story": {"snapList": [snap(i, 1 if i % 3 else 0) for i in range(35)]},
    "curatedHighlights": [{"storyTitle": {"value": "summer"}, "snapList": [snap(100), snap(101)]},
                          {"storyTitle": {"value": "empty"}, "snapList": []}],
    "spotlightHighlights": [{"snapList": [snap(200)]}, {"snapList": []}]}}}) + "</script>"


class Parse(unittest.TestCase):
    def test_urls(self):
        for u in ("https://www.snapchat.com/spotlight/W7_x", "https://snapchat.com/t/AbCd", "https://story.snapchat.com/s/x",
                  "https://www.snapchat.com/add/kylie", "https://www.snapchat.com/@kylie"):
            self.assertTrue(snapchat.is_page(u), u)
        self.assertFalse(snapchat.is_page("https://www.tiktok.com/@a"))
        self.assertFalse(snapchat.is_page("https://notsnapchat.com/x"))
        self.assertTrue(snapchat.is_cdn("https://bolt-gcdn.sc-cdn.net/y/abc"))

    def test_spotlight_and_profile(self):
        p = snapchat.parse("u", SPOT)
        self.assertEqual((p.single.url[:30], p.single.kind, p.title, p.user), ("https://cf-st.sc-cdn.net/d/abc", "video", "Views", "shrey57"))
        p = snapchat.parse("u", PROFILE)
        self.assertIsNone(p.single)
        self.assertEqual((p.user, p.title, p.picture), ("kylie", "King Kylie", "https://pic"))
        self.assertEqual({k: len(v) for k, v in p.groups.items()}, {"📖 ستۆری": 35, "⭐ summer": 2, "🎬 Spotlight": 1})
        self.assertEqual(p.groups["📖 ستۆری"][0].kind, "image")
        self.assertEqual(p.groups["📖 ستۆری"][1].kind, "video")

    def test_nothing_public(self):
        orig = snapchat.fetch
        snapchat.fetch = lambda url, timeout=30: (url, "<html></html>")
        try:
            with self.assertRaises(ytdl.yt_dlp.utils.DownloadError):
                snapchat.load("https://www.snapchat.com/add/private")
        finally:
            snapchat.fetch = orig


class SnapFlow(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        os.environ.update(DATA_DIR=d, BOT_TOKEN="1:x", API_ID="1", API_HASH="h", PER_USER_HOUR="10", MAX_CONCURRENT="1")
        self.cfg = config.load("/nonexistent")
        self.saved = (snapchat.fetch, snapchat.download_file, snapchat.fetch_result, snapchat.video_info)
        pages = {"https://www.snapchat.com/spotlight/X": SPOT, "https://www.snapchat.com/add/kylie": PROFILE}
        snapchat.fetch = lambda url, timeout=30: (url, pages[url])

        def dl(url, base, progress=None, cancel=None, max_bytes=0):
            n = int(url.split("/s")[-1].split(".")[0])
            p = base + (".jpg" if n < 100 and n % 3 == 0 else ".mp4")          # story: every 3rd snap is a photo
            open(p, "wb").write(b"x")
            return p
        snapchat.download_file = dl
        self.results = []

        def fr(cfg, url, kind, workdir, progress=None, cancel=None, title=""):
            os.makedirs(workdir, exist_ok=True)
            p = os.path.join(workdir, "a.mp3" if kind.startswith("a") else "v.mp4")
            open(p, "wb").write(b"x" * 10)
            self.results.append((url, kind))
            return ytdl.Result(p, title, "", 4, None, 480, 880)
        snapchat.fetch_result = fr
        snapchat.video_info = lambda cfg, p: (4, 480, 880)

    def tearDown(self):
        snapchat.fetch, snapchat.download_file, snapchat.fetch_result, snapchat.video_info = self.saved

    def test_single_and_story(self):
        async def go():
            b = bot.Bot(self.cfg)
            app = b.app = FakeApp()
            groups = []

            async def send_media_group(chat, media):
                groups.append(media)
            app.send_media_group = send_media_group
            cq = lambda m, d: types.SimpleNamespace(data=d, from_user=types.SimpleNamespace(id=10), message=m,
                                                    answer=lambda *a, **k: asyncio.sleep(0))
            # spotlight -> card with video + MP3 -> MP3 made from the snap
            await b.on_text(None, Msg(app, text="https://www.snapchat.com/spotlight/X"))
            card = app.sent[-1][1]
            key = next(x for x in data(card) if x.startswith("d:")).split(":")[1]
            self.assertIn(f"d:{key}:vbest", data(card))
            self.assertIn("👻 Snapchat", card.text)
            await b.on_callback(None, cq(card, f"d:{key}:a320"))
            self.assertEqual(app.sent[-1][0], "audio")
            self.assertEqual(self.results[-1], ("https://cf-st.sc-cdn.net/d/abc.1034.IRZXSOY?mo=x", "a320"))
            await b.on_callback(None, cq(card, f"d:{key}:vbest"))
            self.assertEqual(app.sent[-1][0], "video")
            # profile -> groups card -> story: 30 snaps = 3 albums of 10, then a "next" button for the last 5
            await b.on_text(None, Msg(app, text="https://www.snapchat.com/add/kylie"))
            card = app.sent[-1][1]
            sg = [x for x in data(card) if x.startswith("sg:")]
            self.assertEqual(len(sg), 3)
            await b.on_callback(None, cq(card, sg[0]))
            self.assertEqual([len(g) for g in groups], [10, 10, 10])
            self.assertTrue(isinstance(groups[0][0], bot.InputMediaPhoto) and isinstance(groups[0][1], bot.InputMediaVideo))
            self.assertIn("1–30 / 35", groups[0][0].caption)
            nxt = app.sent[-1][1]
            self.assertIn("5 سناپی تر", nxt.text)
            await b.on_callback(None, cq(nxt, data(nxt)[0]))
            self.assertEqual(len(groups[-1]), 5)
            self.assertEqual((b.jobs, b.active), ({}, {}))
            self.assertEqual(os.listdir(self.cfg.download_dir), [])
        asyncio.run(go())


if __name__ == "__main__":
    unittest.main()
