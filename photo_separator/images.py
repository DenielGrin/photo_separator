"""Bounded downloader: public IPs only, DNS pinned, redirects revalidated, no proxies/cookies."""

import base64
import hashlib
import http.client
import io
import ipaddress
import socket
import ssl
import time
import warnings
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from PIL import Image, ImageFilter, ImageOps, ImageStat

MAX_BYTES = 12 * 1024 * 1024
MAX_PIXELS = 25_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class ImageError(ValueError):
    pass


def resolve_public(url: str):
    try:
        p = urlsplit(url)
        if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password or p.fragment:
            raise ImageError("Invalid image URL")
        port = p.port or (443 if p.scheme == "https" else 80)
        if port not in {80, 443}:
            raise ImageError("Only HTTP(S) default ports allowed")
        addresses = socket.getaddrinfo(p.hostname, port, type=socket.SOCK_STREAM)
        ips = list(dict.fromkeys(a[4][0] for a in addresses))
        if not ips or any(
            not ipaddress.ip_address(ip).is_global or ipaddress.ip_address(ip).is_multicast for ip in ips
        ):
            raise ImageError("Private, local and reserved image addresses are blocked")
        return p, port, ips[0]
    except (ValueError, OSError) as exc:
        if isinstance(exc, ImageError):
            raise
        raise ImageError("Image host cannot be resolved safely") from None


class PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, port, ip, timeout):
        super().__init__(host, port, timeout=timeout)
        self.ip = ip

    def connect(self):
        self.sock = socket.create_connection((self.ip, self.port), self.timeout)


class PinnedHTTPS(PinnedHTTP):
    def connect(self):
        super().connect()
        self.sock = ssl.create_default_context().wrap_socket(self.sock, server_hostname=self.host)


def download(url: str) -> bytes:
    deadline = time.monotonic() + 25
    for _ in range(4):
        p, port, ip = resolve_public(url)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ImageError("Image download deadline exceeded")
        conn_cls = PinnedHTTPS if p.scheme == "https" else PinnedHTTP
        conn = conn_cls(p.hostname, port, ip, timeout=min(8, remaining))
        try:
            path = p.path or "/"
            if p.query:
                path += "?" + p.query
            conn.request(
                "GET", path, headers={"User-Agent": "PhotoSeparatorResearch/0.1", "Accept": "image/*"}
            )
            response = conn.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location:
                    raise ImageError("Redirect without target")
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise ImageError(f"Image HTTP status {response.status}")
            declared = response.getheader("Content-Length")
            if declared and int(declared) > MAX_BYTES:
                raise ImageError("Image exceeds 12 MiB")
            body = bytearray()
            while True:
                if time.monotonic() > deadline:
                    raise ImageError("Image download deadline exceeded")
                chunk = response.read(min(64 * 1024, MAX_BYTES + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > MAX_BYTES:
                    raise ImageError("Image exceeds 12 MiB")
            return bytes(body)
        except (OSError, ValueError, http.client.HTTPException):
            raise ImageError("Image download failed or exceeded limits") from None
        finally:
            conn.close()
    raise ImageError("Too many image redirects")


@dataclass
class PreparedImage:
    data_url: str
    digest: str
    dhash: int
    width: int
    height: int
    brightness: float
    edge_variance: float
    jpeg: bytes

    def metrics(self):
        return {k: getattr(self, k) for k in ("digest", "width", "height", "brightness", "edge_variance")}


def prepare(raw: bytes) -> PreparedImage:
    if len(raw) > MAX_BYTES:
        raise ImageError("Image exceeds 12 MiB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as source:
                if source.format not in {"JPEG", "PNG", "WEBP", "GIF"}:
                    raise ImageError("Unsupported image format")
                if getattr(source, "n_frames", 1) > 1:
                    raise ImageError("Animated images require review")
                if source.width * source.height > MAX_PIXELS:
                    raise ImageError("Image exceeds pixel limit")
                image = ImageOps.exif_transpose(source).convert("RGB")
                image.load()
    except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ImageError("Invalid, unsupported or oversized image") from None
    width, height = image.size
    digest = hashlib.sha256(image.tobytes() + f"{width}x{height}".encode()).hexdigest()
    thumb = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    pixels = list(thumb.tobytes())
    dhash = sum(
        (pixels[y * 9 + x] > pixels[y * 9 + x + 1]) << (y * 8 + x) for y in range(8) for x in range(8)
    )
    gray = image.convert("L")
    gray.thumbnail((512, 512))
    brightness = ImageStat.Stat(gray).mean[0]
    edges = gray.filter(ImageFilter.FIND_EDGES)
    if gray.width > 2 and gray.height > 2:
        edges = edges.crop((1, 1, gray.width - 1, gray.height - 1))
    variance = ImageStat.Stat(edges).var[0]
    image.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=90)
    jpeg = out.getvalue()
    return PreparedImage(
        "data:image/jpeg;base64," + base64.b64encode(jpeg).decode(),
        digest,
        dhash,
        width,
        height,
        round(brightness, 2),
        round(variance, 2),
        jpeg,
    )
