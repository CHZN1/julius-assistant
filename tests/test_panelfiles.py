"""Unit tests for the panel file server and the image pipeline."""

import http.client
import io
import os
import tempfile
import time
import unittest
from pathlib import Path

from PIL import Image

import julius.ui_bridge.panelfiles as pf


def png(size=(40, 30), mode="RGB", color=(200, 10, 10)):
    b = io.BytesIO()
    Image.new(mode, size, color).save(b, "PNG")
    return b.getvalue()


class Ingest(unittest.TestCase):
    def test_reencodes_and_strips_trailing_payload(self):
        data, ext = pf.ingest_image(png() + b"<script>alert(1)</script>")
        self.assertEqual(ext, "jpg")
        self.assertNotIn(b"<script>", data)
        self.assertEqual(Image.open(io.BytesIO(data)).size, (40, 30))

    def test_alpha_stays_png_and_big_is_shrunk(self):
        data, ext = pf.ingest_image(png((3000, 100), "RGBA", (1, 2, 3, 128)))
        self.assertEqual(ext, "png")
        self.assertEqual(max(Image.open(io.BytesIO(data)).size), pf.OUT_SIDE)

    def test_refuses(self):
        for bad in (
            b"",
            b"not an image",
            b"<svg xmlns='http://www.w3.org/2000/svg'><script/></svg>",
            b"GIF89a" + b"\0" * 5,
        ):
            with self.assertRaises(pf.ImageRefused):
                pf.ingest_image(bad)
        with self.assertRaises(pf.ImageRefused):
            pf.ingest_image(png((9000, 2)))  # a side over the limit
        with self.assertRaises(pf.ImageRefused):
            pf.ingest_image(b"x" * (pf.MAX_INPUT_BYTES + 1))

    def test_bmp_refused(self):
        b = io.BytesIO()
        Image.new("RGB", (4, 4)).save(b, "BMP")
        with self.assertRaises(pf.ImageRefused):
            pf.ingest_image(b.getvalue())


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = [time.time()]
        self.files = pf.PanelFiles(
            Path(self.tmp.name) / "panel", token="tok", max_mb=1, ttl_s=100, clock=lambda: self.t[0]
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_dir_private_and_put_roundtrip(self):
        self.assertEqual(os.stat(self.files.root).st_mode & 0o777, 0o700)
        ref = self.files.put(b"abc", "png")
        self.assertRegex(ref, r"^/p/[A-Za-z0-9_-]{8,64}\.png$")
        p = self.files.local_path(ref)
        self.assertEqual(p.read_bytes(), b"abc")
        self.assertEqual(os.stat(p).st_mode & 0o777, 0o600)

    def test_token_and_names(self):
        ref = self.files.put(b"abc", "png")
        name = ref.split("/")[-1]
        self.assertIsNotNone(self.files.open_ref("tok", name))
        self.assertIsNone(self.files.open_ref("wrong", name))
        for bad in ("../x.png", "a.svg", "..%2f.png", "x" * 3 + ".png", name + "/"):
            self.assertIsNone(self.files.open_ref("tok", bad))

    def test_symlink_not_followed(self):
        secret = Path(self.tmp.name) / "secret.png"
        secret.write_bytes(b"secret")
        os.symlink(secret, self.files.root / "linklinklink.png")
        self.assertIsNone(self.files.open_ref("tok", "linklinklink.png"))

    def test_ttl_and_quota(self):
        ref = self.files.put(b"old", "png")
        self.t[0] += 1000
        self.files.prune()
        self.assertIsNone(self.files.local_path(ref))
        self.t[0] = time.time()
        a = self.files.put(b"a" * 700_000, "png")
        os.utime(self.files.local_path(a), (self.t[0] - 50, self.t[0] - 50))
        b = self.files.put(b"b" * 700_000, "png")
        self.assertIsNone(self.files.local_path(a))  # oldest evicted over the 1 MB quota
        self.assertIsNotNone(self.files.local_path(b))


class Http(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.files = pf.PanelFiles(Path(self.tmp.name) / "p", token="tok123")
        self.server = pf.PanelFileServer(self.files, port=0).start()
        self.ref = self.files.put(png(), "png")

    def tearDown(self):
        self.server.stop()
        self.tmp.cleanup()

    def get(self, path, method="GET", **headers):
        c = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=3)
        c.request(method, path, headers=headers)
        r = c.getresponse()
        body = r.read()
        c.close()
        return r, body

    def test_serves_with_hardened_headers(self):
        r, body = self.get("/tok123" + self.ref)
        self.assertEqual(r.status, 200)
        self.assertEqual(r.getheader("Content-Type"), "image/png")
        self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")
        self.assertIn("default-src 'none'", r.getheader("Content-Security-Policy"))
        self.assertEqual(r.getheader("Cache-Control"), "no-store")
        self.assertEqual(
            r.getheader("Cross-Origin-Resource-Policy"), "cross-origin"
        )  # the app page is file://, which is not same-site; the token and Host check are the gate
        self.assertTrue(body.startswith(b"\x89PNG"))
        self.assertTrue(self.server.base.endswith("/tok123"))

    def test_refusals(self):
        self.assertEqual(self.get("/wrong" + self.ref)[0].status, 404)
        self.assertEqual(self.get("/")[0].status, 404)
        self.assertEqual(self.get("/tok123/p/")[0].status, 404)
        self.assertEqual(self.get("/tok123/p/../../etc/passwd")[0].status, 404)
        self.assertEqual(self.get("/tok123" + self.ref + "?x=1")[0].status, 404)
        self.assertEqual(self.get("/tok123" + self.ref, Host="evil.example")[0].status, 403)
        self.assertEqual(
            self.get("/tok123" + self.ref, Origin="https://evil.example")[0].status, 403
        )
        self.assertEqual(
            self.get("/tok123" + self.ref, Origin="http://localhost:3000")[0].status, 200
        )
        for m in ("POST", "PUT", "DELETE"):
            self.assertEqual(self.get("/tok123" + self.ref, method=m)[0].status, 405)


class Cache(unittest.TestCase):
    def test_cache_images_skips_failures(self):
        class F:
            def fetch_image(self, url, max_bytes):
                if "bad" in url:
                    raise OSError("nope")
                if "junk" in url:
                    return url, "image/png", b"junk"
                return url, "image/png", png((400, 300))

        with tempfile.TemporaryDirectory() as d:
            files = pf.PanelFiles(Path(d) / "p")
            got = pf.cache_images(
                F(),
                files,
                [
                    {
                        "url": "https://a/ok.png",
                        "alt": "A",
                        "page": "https://a/",
                        "page_title": "A",
                    },
                    {"url": "https://a/bad.png"},
                    {"url": "https://a/junk.png"},
                ],
            )
            self.assertEqual(len(got), 1)
            self.assertEqual(got[0]["alt"], "A")
            self.assertIsNotNone(files.local_path(got[0]["image"]))


class ContentFilter(unittest.TestCase):
    def test_small_and_extreme_aspect_are_refused_only_with_the_filter(self):
        pf.ingest_image(png((40, 30)))  # file previews keep small pictures
        for size in ((199, 400), (400, 120), (1500, 400), (300, 1000)):
            with self.subTest(size=size), self.assertRaises(pf.ImageRefused):
                pf.ingest_image(png(size), content_filter=True)
        for size in ((200, 200), (600, 300), (900, 300)):
            with self.subTest(size=size):
                pf.ingest_image(png(size), content_filter=True)

    def test_logo_and_icon_names_are_recognised(self):
        for url, alt in (
            ("https://a/static/site-logo.png", ""),
            ("https://a/img/ICON_32.png", ""),
            ("https://a/favicon.ico", ""),
            ("https://a/x/hero.jpg", "Company logo"),
            ("https://a/sprite.png?v=3", ""),
        ):
            self.assertTrue(pf.looks_like_chrome(url, alt), url)
        for url, alt in (
            ("https://a/silicon-valley.jpg", "Silicon Valley at dawn"),
            ("https://a/skyline.jpg", "The tower"),
            ("https://a/logos/../photo.jpg", ""),
        ):
            self.assertFalse(pf.looks_like_chrome(url, alt), url)

    def test_cache_skips_chrome_and_keeps_going_until_eight_good_ones(self):
        calls = []

        class F:
            def fetch_image(self, url, max_bytes):
                calls.append(url)
                if "tiny" in url:
                    return url, "image/png", png((48, 48))
                return url, "image/png", png((500, 400))

        cands = [
            {"url": "https://a/logo.png"},
            {"url": "https://a/tiny1.png"},
            {"url": "https://a/tiny2.png"},
        ]
        cands += [{"url": f"https://a/p{i}.png", "alt": f"photo {i}"} for i in range(10)]
        with tempfile.TemporaryDirectory() as d:
            got = pf.cache_images(F(), pf.PanelFiles(Path(d) / "p"), cands)
        self.assertEqual(len(got), 8)
        self.assertNotIn("https://a/logo.png", calls)  # not even fetched
        self.assertEqual([g["alt"] for g in got][:2], ["photo 0", "photo 1"])


class FetchImage(unittest.TestCase):
    def test_ssrf_and_size_rules_apply_to_images(self):
        import socket

        import julius.agents.research as r

        f = r.Fetcher(transport=lambda *a, **k: (200, {"content-type": "image/png"}, b"x" * 20))
        with self.assertRaises(r.Blocked):
            f.fetch_image("http://127.0.0.1/a.png")
        with self.assertRaises(r.Blocked):
            f.fetch_image("http://localhost:8765/a.png")
        public = lambda *a, **k: [(socket.AF_INET, 1, 6, "", ("93.184.216.34", 0))]  # noqa: E731
        ok = r.Fetcher(
            resolve=public,
            transport=lambda *a, **k: (200, {"content-type": "image/png"}, b"x" * 20),
        )
        self.assertEqual(ok.fetch_image("https://example.org/a.png")[1], "image/png")
        with self.assertRaises(r.Blocked):
            ok.fetch_image("https://example.org/a.png", max_bytes=10)


if __name__ == "__main__":
    unittest.main()
