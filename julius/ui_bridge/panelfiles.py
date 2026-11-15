"""Panel file server: the only place the side panel loads images and charts from.

A small HTTP server on 127.0.0.1 (default port 8785) that serves raster images from one private directory, by opaque id, under a per-run
random token in the path: GET /<token>/p/<id>.<png|jpg|webp|gif>. Nothing else is served: no listing, no paths, no SVG, no symlinks. Remote
images (from research pages) are never passed through: `ingest_image` decodes them with Pillow and re-encodes a fresh file, which strips
metadata and anything appended to the original, and enforces size and pixel limits."""

import hmac
import io
import os
import re
import secrets
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from julius.ui_bridge.events import HOST, origin_ok

DEFAULT_PORT = 8785
MAX_INPUT_BYTES = 5_000_000
MAX_SIDE = 8000  # decoded pixels per side we are willing to open
MAX_PIXELS = 40_000_000
OUT_SIDE = 1280
MAX_IMAGES = 8
MAX_CANDIDATES = (
    16  # fetch attempts per card: filtered-out logos and icons do not use up the eight places
)
MAX_TOTAL_INPUT = 20_000_000
ALLOWED = {
    "PNG": "png",
    "JPEG": "jpg",
    "WEBP": "webp",
    "GIF": "png",
}  # GIFs are flattened to their first frame, saved as PNG
NAME = re.compile(r"^[A-Za-z0-9_-]{8,64}\.(png|jpg|webp|gif)$")
CTYPE = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp", "gif": "image/gif"}


class ImageRefused(ValueError):
    pass


MIN_SIDE = 200  # research pictures: anything smaller on either side is an icon, a button or a tracking pixel
MAX_ASPECT = 3.0  # and anything wider or taller than 3:1 is a banner, a divider or a sprite strip
LOGO_HINT = re.compile(
    r"(?<![a-z])(logos?|icons?|favicons?|sprites?|avatars?|emoji|badges?|buttons?|spacer|pixel|tracking|placeholder|wordmark|masthead)(?![a-z])",
    re.I,
)


def looks_like_chrome(url: str, alt: str = "") -> bool:
    """True when the URL's file name or the alt text says logo / icon / sprite / avatar, etc. (checked before fetching)."""
    from urllib.parse import unquote, urlsplit

    path = unquote(urlsplit(url or "").path or "")
    return bool(LOGO_HINT.search(path.rsplit("/", 1)[-1]) or LOGO_HINT.search(alt or ""))


def too_small_or_odd(width: int, height: int) -> str | None:
    """Why a decoded picture is not worth a card (or None when it is fine)."""
    if min(width, height) < MIN_SIDE:
        return "small"
    if max(width, height) / max(1, min(width, height)) > MAX_ASPECT:
        return "aspect"
    return None


def ingest_image(raw: bytes, content_filter: bool = False) -> tuple[bytes, str]:
    """-> (re-encoded bytes, extension). Raises ImageRefused for anything that is not a sane raster image; with content_filter
    also for pictures under MIN_SIDE px or with an extreme aspect ratio (logos, icons, banners)."""
    from PIL import Image, UnidentifiedImageError

    if not raw or len(raw) > MAX_INPUT_BYTES:
        raise ImageRefused("image is empty or too large")
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with Image.open(io.BytesIO(raw)) as im:
            if im.format not in ALLOWED:
                raise ImageRefused(f"unsupported image type {im.format}")
            w, h = im.size
            if w < 1 or h < 1 or w > MAX_SIDE or h > MAX_SIDE or w * h > MAX_PIXELS:
                raise ImageRefused("image dimensions out of range")
            why = too_small_or_odd(w, h) if content_filter else None
            if why:
                raise ImageRefused(f"not a content picture ({why})")
            im.seek(0)
            im.load()
            frame = im.copy()
    except ImageRefused:
        raise
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombError,
        SyntaxError,
    ) as e:
        raise ImageRefused(f"not a readable image ({type(e).__name__})") from e
    frame.thumbnail((OUT_SIDE, OUT_SIDE))
    out = io.BytesIO()
    if frame.mode in ("RGBA", "LA", "P") and (frame.mode != "P" or "transparency" in frame.info):
        frame.convert("RGBA").save(out, "PNG", optimize=True)
        return out.getvalue(), "png"
    frame.convert("RGB").save(out, "JPEG", quality=86, optimize=True)
    return out.getvalue(), "jpg"


class PanelFiles:
    """Storage (a 0700 directory, bounded by size and age) plus the token and HTTP handler logic."""

    def __init__(
        self,
        root: Path,
        token: str | None = None,
        max_mb: int = 200,
        ttl_s: int = 24 * 3600,
        clock: Callable = time.time,
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.token = token or secrets.token_urlsafe(24)
        self.max_bytes = max_mb * 1_000_000
        self.ttl_s = ttl_s
        self.clock = clock
        self._lock = threading.Lock()
        self._mem: dict[
            str, tuple[bytes, float]
        ] = {}  # in-memory images (vision looks): never written to disk
        self._live: dict[
            str, bytes
        ] = {}  # the newest few camera preview frames (a separate, tiny slot: previews never push real pictures out of memory)
        self._live_n = 0
        self.prune()

    def put_memory(self, data: bytes, ext: str, ttl_s: int = 3600, keep: int = 6) -> str:
        """Hold bytes in memory only and return the same kind of `/p/<id>.<ext>` reference. Gone when the process ends, after
        `ttl_s`, or when more than `keep` newer ones exist. The only way such an image reaches disk is the user saying save this card."""
        if ext not in CTYPE:
            raise ImageRefused("bad extension")
        name = f"{secrets.token_urlsafe(12)}.{ext}"
        now = self.clock()
        with self._lock:
            for k in [k for k, (_, t) in self._mem.items() if now - t > ttl_s]:
                del self._mem[k]
            self._mem[name] = (data, now)
            while len(self._mem) > keep:
                del self._mem[next(iter(self._mem))]
        return f"/p/{name}"

    def put_live(self, data: bytes) -> str:
        """A camera preview frame: held in memory only, the newest 3 kept, each under a fresh name so the page never shows a cached frame."""
        with self._lock:
            self._live_n += 1
            name = f"live{self._live_n:08d}{secrets.token_hex(3)}.jpg"
            self._live[name] = data
            while len(self._live) > 3:
                del self._live[next(iter(self._live))]
        return f"/p/{name}"

    def read_ref(self, token: str, name: str) -> tuple[bytes, str] | None:
        """(bytes, extension) for token + name, from memory or disk, or None."""
        if not hmac.compare_digest(token.encode(), self.token.encode()) or not NAME.match(name):
            return None
        with self._lock:
            held = self._mem.get(name)
            live = self._live.get(name)
        if held is not None:
            return held[0], name.rsplit(".", 1)[1]
        if live is not None:
            return live, "jpg"
        p = self.open_ref(token, name)
        if p is None:
            return None
        try:
            return p.read_bytes(), p.suffix[1:]
        except OSError:
            return None

    def bytes_of(self, ref: str) -> tuple[bytes, str] | None:
        """The stored bytes behind a panel reference (memory or disk), for saving a card."""
        m = re.fullmatch(r"/p/([A-Za-z0-9_-]{8,64}\.(?:png|jpg|webp|gif))", ref or "")
        return self.read_ref(self.token, m.group(1)) if m else None

    def put(self, data: bytes, ext: str) -> str:
        """Store bytes and return the reference `/p/<id>.<ext>` that goes into panel messages."""
        if ext not in CTYPE:
            raise ImageRefused("bad extension")
        name = f"{secrets.token_urlsafe(12)}.{ext}"
        path = self.root / name
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        self.prune()
        return f"/p/{name}"

    def prune(self) -> None:
        with self._lock:
            now = self.clock()
            files = []
            for p in self.root.iterdir():
                try:
                    st = p.lstat()
                except OSError:
                    continue
                if now - st.st_mtime > self.ttl_s:
                    p.unlink(missing_ok=True)
                else:
                    files.append((st.st_mtime, st.st_size, p))
            total = sum(s for _, s, _ in files)
            for _, size, p in sorted(files):
                if total <= self.max_bytes:
                    break
                p.unlink(missing_ok=True)
                total -= size

    def open_ref(self, token: str, name: str) -> Path | None:
        """The file for token + name, or None. Opaque names only; never follows a symlink."""
        if not hmac.compare_digest(token.encode(), self.token.encode()) or not NAME.match(name):
            return None
        p = self.root / name
        try:
            st = p.lstat()
        except OSError:
            return None
        import stat as _stat

        return p if _stat.S_ISREG(st.st_mode) else None

    def local_path(self, ref: str) -> Path | None:
        """Resolve a panel reference (`/p/<id>.<ext>`) to its stored file, for saving a card."""
        m = re.fullmatch(r"/p/([A-Za-z0-9_-]{8,64}\.(?:png|jpg|webp|gif))", ref or "")
        return self.open_ref(self.token, m.group(1)) if m else None


class _Handler(BaseHTTPRequestHandler):
    server_version = "JuliusPanel"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, *a: Any) -> None:  # never log paths: they contain the token
        pass

    def _fail(self, code: int) -> None:
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_GET(self) -> None:
        files: PanelFiles = self.server.files  # type: ignore[attr-defined]
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        if host not in (HOST, "localhost", "::1"):
            return self._fail(403)
        origin = self.headers.get("Origin")
        if origin is not None and not origin_ok(origin):
            return self._fail(403)
        parts = urlsplit(self.path)
        bits = parts.path.split("/")  # ['', token, 'p', name]
        if parts.query or len(bits) != 4 or bits[0] != "" or bits[2] != "p":
            return self._fail(404)
        got = files.read_ref(bits[1], bits[3])
        if got is None:
            return self._fail(404)
        data, ext = got
        self.send_response(200)
        self.send_header("Content-Type", CTYPE[ext])
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'")
        self.send_header("Cross-Origin-Resource-Policy", "cross-origin")
        self.end_headers()
        self.wfile.write(data)

    def _no(self) -> None:
        self._fail(405)

    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _no


class _Httpd(ThreadingHTTPServer):
    def handle_error(
        self, request: Any, client_address: Any
    ) -> None:  # a browser dropping a connection is not an event worth a traceback
        pass


class PanelFileServer:
    def __init__(self, files: PanelFiles, port: int = DEFAULT_PORT) -> None:
        self.files = files
        self.httpd = _Httpd((HOST, port), _Handler)
        self.httpd.files = files  # type: ignore[attr-defined]
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self._thread = threading.Thread(
            target=self.httpd.serve_forever, name="panel-files", daemon=True
        )

    @property
    def base(self) -> str:
        """Goes into `hello.panel.base`; image refs are appended to it."""
        return f"http://{HOST}:{self.port}/{self.files.token}"

    def start(self) -> "PanelFileServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def cache_images(
    fetcher: Any, files: PanelFiles, candidates: list[dict], log: Callable = lambda *a, **k: None
) -> list[dict]:
    """Fetch remote images through the research fetcher (SSRF-checked, GET only), re-encode them and store them. Returns panel image
    dicts `{image, alt, page_url, page_title}`; any image that fails is skipped. Candidates are `{url, alt, page, page_title}`."""
    out: list[dict] = []
    total = 0
    tried = 0
    for c in candidates:
        if len(out) >= MAX_IMAGES or total >= MAX_TOTAL_INPUT or tried >= MAX_CANDIDATES:
            break
        if looks_like_chrome(c.get("url", ""), c.get("alt", "")):
            log("panel_image_skipped", reason="logo_hint")
            continue
        tried += 1
        try:
            _, _, raw = fetcher.fetch_image(c["url"], max_bytes=MAX_INPUT_BYTES)
            total += len(raw)
            data, ext = ingest_image(raw, content_filter=True)
            out.append(
                {
                    "image": files.put(data, ext),
                    "alt": c.get("alt", ""),
                    "page_url": c.get("page", ""),
                    "page_title": c.get("page_title", ""),
                }
            )
        except Exception as e:  # blocked host, network error, bad image: skip it
            log("panel_image_skipped", reason=type(e).__name__)
    return out
