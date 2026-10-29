import io
import socket
from unittest.mock import patch

import pytest
from PIL import Image

from photo_separator.images import ImageError, PinnedHTTP, download, prepare, resolve_public


def png(size=(800, 600), color="red"):
    f = io.BytesIO()
    Image.new("RGB", size, color).save(f, format="PNG")
    return f.getvalue()


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://user:secret@example.com/a.jpg",
        "http://example.com:8080/a",
        "http://example.com/a#fragment",
    ],
)
def test_invalid_url_blocked(url):
    with pytest.raises(ImageError):
        resolve_public(url)


@pytest.mark.parametrize(
    "ip", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "192.168.1.1", "0.0.0.0", "224.0.0.1"]
)
def test_nonpublic_ip_blocked(ip):
    with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 80))]):
        with pytest.raises(ImageError):
            resolve_public("http://example.com/a.jpg")


def test_mixed_dns_blocked():
    with patch(
        "socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 80)), (2, 1, 6, "", ("127.0.0.1", 80))]
    ):
        with pytest.raises(ImageError):
            resolve_public("http://example.com/a")


def test_pinned_connection_never_resolves_hostname_again():
    c = PinnedHTTP("example.com", 80, "8.8.8.8", 2)
    with patch("socket.create_connection") as create:
        c.connect()
        create.assert_called_once_with(("8.8.8.8", 80), 2)


def test_redirect_revalidates_target():
    class Response:
        status = 302

        def getheader(self, k):
            return "http://127.0.0.1/secret"

    class Conn:
        def __init__(self, *a, **kw):
            pass

        def request(self, *a, **kw):
            pass

        def getresponse(self):
            return Response()

        def close(self):
            pass

    with (
        patch(
            "photo_separator.images.resolve_public",
            side_effect=[
                (
                    type("P", (), {"scheme": "http", "hostname": "example.com", "path": "/a", "query": ""})(),
                    80,
                    "8.8.8.8",
                ),
                ImageError("private"),
            ],
        ) as resolver,
        patch("photo_separator.images.PinnedHTTP", Conn),
    ):
        with pytest.raises(ImageError):
            download("http://example.com/a")
        assert resolver.call_args_list[1].args[0] == "http://127.0.0.1/secret"


def test_decode_rejects_non_image_and_preserves_dimensions():
    with pytest.raises(ImageError):
        prepare(b"<html>not a photo</html>")
    result = prepare(png())
    assert (result.width, result.height) == (800, 600)
    assert result.data_url.startswith("data:image/jpeg;base64,")
    assert prepare(png()).digest == result.digest


def test_animated_image_is_not_silently_first_frame():
    f = io.BytesIO()
    Image.new("RGB", (20, 20), "red").save(
        f, format="GIF", save_all=True, append_images=[Image.new("RGB", (20, 20), "blue")]
    )
    with pytest.raises(ImageError):
        prepare(f.getvalue())


def test_tiny_images_dont_crash_pipeline():
    assert prepare(png((1, 1))).width == 1
