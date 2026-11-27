"""The whole bot flow with a fake Telegram and a fake downloader (no network, no token)."""
import asyncio, os, sys, tempfile, types, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bot, config, ytdl


class Msg:
    def __init__(self, app, chat=1, user=10, text="", photo=False):
        self.app, self.text, self.photo = app, text, photo
        self.chat = types.SimpleNamespace(id=chat, type=types.SimpleNamespace(value="private"))
        self.from_user = types.SimpleNamespace(id=user, first_name="U")
        self.audio = self.video = self.document = None
        self.markup = None
        self.deleted = False

    async def reply_text(self, text, reply_markup=None, **_):
        return await self.app.send_message(self.chat.id, text, reply_markup=reply_markup)

    async def edit_text(self, text, reply_markup=None, **_):
        self.text, self.markup = text, reply_markup

    async def edit_caption(self, text, reply_markup=None, **_):
        self.text, self.markup = text, reply_markup

    async def delete(self):
        self.deleted = True


class FakeApp:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat, text, reply_markup=None, **_):
        m = Msg(self, chat, text=text); m.markup = reply_markup; self.sent.append(("text", m)); return m

    async def send_photo(self, chat, photo, caption="", reply_markup=None, **_):
        m = Msg(self, chat, text=caption, photo=True); m.markup = reply_markup; self.sent.append(("photo", m)); return m

    async def _send(self, kind, chat, f, caption="", reply_markup=None, progress=None, **_):
        if progress:
            await progress(50, 100)
            await progress(100, 100)
        m = Msg(self, chat, text=caption); m.markup = reply_markup
        setattr(m, kind, types.SimpleNamespace(file_id=f"FID-{os.path.basename(str(f))}"))
        self.sent.append((kind, m)); return m

    async def send_audio(self, chat, f, **kw):
        return await self._send("audio", chat, f, **kw)

    async def send_video(self, chat, f, **kw):
        return await self._send("video", chat, f, **kw)

    def stop_transmission(self):
        raise RuntimeError("stop")


def data(m):
    return [b.callback_data for row in m.markup.inline_keyboard for b in row if b.callback_data]


class Flow(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        os.environ.update(DATA_DIR=d, BOT_TOKEN="1:x", API_ID="1", API_HASH="h", PER_USER_HOUR="3", MAX_CONCURRENT="1")
        self.cfg = config.load("/nonexistent")
        self.downloads = []

        def fake_download(cfg, url, kind, workdir, progress=None, cancel=None):
            if cancel is not None and cancel.is_set():
                raise ytdl.Cancelled()
            os.makedirs(workdir, exist_ok=True)
            ext = "mp3" if kind.startswith("a") else "mp4"
            p = os.path.join(workdir, f"media.{ext}")
            open(p, "wb").write(b"x" * 2048)
            if progress:
                progress({"status": "downloading", "downloaded_bytes": 1024, "total_bytes": 2048, "speed": 1e6, "eta": 1})
            self.downloads.append((url, kind))
            return ytdl.Result(p, "Song", "Artist", 200, None, 1280, 720)

        self.patch = {
            "search": lambda cfg, q, limit=16: [ytdl.Item(f"{i:011d}", f"Song {i}", 200, "Ch") for i in range(10)],
            "probe": lambda cfg, url: ytdl.Media(url, "Clip", "up", 30, "🎵 TikTok", "", [720, 480, 360], True),
            "download": fake_download}
        self.saved = {k: getattr(ytdl, k) for k in self.patch}
        for k, v in self.patch.items():
            setattr(ytdl, k, v)

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(ytdl, k, v)

    def run_(self, coro):
        return asyncio.run(coro)

    def test_full_flow(self):
        async def go():
            b = bot.Bot(self.cfg)
            app = b.app = FakeApp()
            cq = lambda m, d: types.SimpleNamespace(data=d, from_user=types.SimpleNamespace(id=10), message=m,
                                                    answer=lambda *a, **k: asyncio.sleep(0))
            # 1) search -> results with 2 pages
            await b.on_text(None, Msg(app, text="zakaria"))
            res = app.sent[-1][1]
            self.assertIn("ئەنجامەکان", res.text)
            self.assertEqual(sum(x.startswith("l:") for x in data(res)), 8)
            nxt = next(x for x in data(res) if x.startswith("s:") and x.endswith(":1"))
            await b.on_callback(None, cq(res, nxt))
            self.assertEqual(sum(x.startswith("l:") for x in data(res)), 2)
            # 2) pick a result -> card (photo) with MP3 + video buttons
            pick = next(x for x in data(res) if x.startswith("l:"))
            await b.on_callback(None, cq(res, pick))
            kind, card = app.sent[-1]
            self.assertEqual(kind, "photo")
            self.assertIn(f"d:{pick[2:]}:a320", data(card))
            # 3) MP3 -> downloaded and sent with the "video" quick button
            await b.on_callback(None, cq(card, f"d:{pick[2:]}:a320"))
            kind, sent = app.sent[-1]
            self.assertEqual(kind, "audio")
            self.assertIn("Artist – Song", sent.text)
            self.assertIn(f"d:{pick[2:]}:v720", data(sent))
            self.assertEqual(len(self.downloads), 1)
            # 4) same again -> from cache, no new download
            await b.on_callback(None, cq(card, f"d:{pick[2:]}:a320"))
            self.assertEqual(app.sent[-1][0], "audio")
            self.assertEqual(len(self.downloads), 1)
            # 5) a TikTok link with mode "video" -> straight to video in the user's quality
            b.store.set(10, "mode", "video")
            await b.on_text(None, Msg(app, text="look https://www.tiktok.com/@a/video/123"))
            self.assertEqual(app.sent[-1][0], "video")
            self.assertEqual(self.downloads[-1], ("https://www.tiktok.com/@a/video/123", "v720"))
            # 6) hourly limit (3): the next new download is refused
            b.store.set(10, "mode", "audio")
            n = len(self.downloads)
            await b.on_text(None, Msg(app, text="https://www.tiktok.com/@a/video/999"))
            await b.on_text(None, Msg(app, text="https://www.tiktok.com/@a/video/998"))
            self.assertEqual(len(self.downloads), n + 1)
            self.assertIn("سنووری 3", app.sent[-1][1].text)
            self.assertEqual(b.jobs, {})
            self.assertEqual(b.active, {})
            self.assertEqual(os.listdir(self.cfg.download_dir), [])        # temp files removed
        self.run_(go())

    def test_cancel_and_settings(self):
        async def go():
            b = bot.Bot(self.cfg)
            app = b.app = FakeApp()
            answers = []
            cq = lambda m, d: types.SimpleNamespace(data=d, from_user=types.SimpleNamespace(id=10), message=m,
                                                    answer=lambda *a, **k: answers.append(a) or asyncio.sleep(0))
            await b.on_text(None, Msg(app, text=bot.BTN_SETTINGS))
            st = app.sent[-1][1]
            self.assertIn("set:mode:audio", data(st))
            await b.on_callback(None, cq(st, "set:aq:a128"))
            self.assertEqual(b.store.settings(10)["aq"], "a128")
            await b.on_callback(None, cq(st, "set:aq:a999"))                  # invalid value ignored
            self.assertEqual(b.store.settings(10)["aq"], "a128")
            # a job that gets cancelled before it starts
            key = b.store.link("https://soundcloud.com/x/y", "Y")
            orig = ytdl.download

            def slow(cfg, url, kind, workdir, progress=None, cancel=None):
                for jid, job in list(b.jobs.items()):
                    job["cancel"].set()
                return orig(cfg, url, kind, workdir, progress, cancel)
            ytdl.download = slow
            await b.deliver(1, 10, key, "a320")
            self.assertIn("هەڵوەشێنرایەوە", app.sent[-1][1].text)
            self.assertIsNone(b.store.cached(key, "a320"))
            self.assertEqual((b.jobs, b.active), ({}, {}))
        self.run_(go())


if __name__ == "__main__":
    unittest.main()
