"""Read image metadata locally and turn it into public search links.

A local file is never uploaded. A public http(s) image URL can be fetched so
the same metadata can be read here, and reverse-image links point at that URL.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
import urllib.request
from io import BytesIO
from pathlib import Path
from urllib.parse import quote, quote_plus, unquote, urlparse

from .models import Row

MAX_BYTES = 40 * 1024 * 1024
FETCH_TIMEOUT = 12
UA = "ORION-PublicSearch/1.0"

WANTED = {
    "Make",
    "Model",
    "LensMake",
    "LensModel",
    "Software",
    "Artist",
    "Copyright",
    "DateTime",
    "DateTimeOriginal",
    "BodySerialNumber",
    "LensSerialNumber",
    "ImageUniqueID",
    "HostComputer",
    "UserComment",
    "ImageDescription",
    "XPAuthor",
    "XPComment",
    "XPTitle",
    "FNumber",
    "ExposureTime",
    "ISOSpeedRatings",
    "FocalLength",
}

PREFERRED = (
    "Format",
    "Dimensions",
    "File size",
    "SHA-256",
    "Make",
    "Model",
    "LensMake",
    "LensModel",
    "Artist",
    "Author",
    "Copyright",
    "Software",
    "DateTimeOriginal",
    "DateTime",
    "GPS",
    "BodySerialNumber",
    "LensSerialNumber",
    "ImageUniqueID",
    "FNumber",
    "ExposureTime",
    "ISOSpeedRatings",
    "FocalLength",
    "Title",
    "ImageDescription",
    "Description",
    "UserComment",
    "Comment",
    "HostComputer",
    "XPAuthor",
    "XPTitle",
    "XPComment",
    "Source",
    "Creator",
    "Rights",
    "CreatorTool",
    "CreateDate",
)

INFO_KEYS = {
    "title": "Title",
    "author": "Author",
    "description": "Description",
    "copyright": "Copyright",
    "software": "Software",
    "comment": "Comment",
    "source": "Source",
}

XMP_ALIASES = {
    "Creator": "Artist",
    "Rights": "Copyright",
    "CreatorTool": "Software",
    "CreateDate": "DateTimeOriginal",
    "Description": "Description",
}


def reverse_links(url: str) -> list[Row]:
    """Browser reverse-image links for a URL. Does not fetch or upload anything."""
    encoded = quote(url, safe="")
    specs = (
        ("Google Lens", "https://lens.google.com/uploadbyurl?url=" + encoded),
        ("TinEye", "https://tineye.com/search?url=" + encoded),
        ("Yandex", "https://yandex.com/images/search?rpt=imageview&url=" + encoded),
        ("Bing", "https://www.bing.com/images/search?view=detailv2&iss=s&q=imgurl:" + encoded),
        ("Google Images", "https://www.google.com/searchbyimage?image_url=" + encoded),
    )
    return [_link(source, target, "reverse image", "web") for source, target in specs]


def image_rows(query) -> list[Row]:
    raw = query.raw.strip()
    if raw.lower().startswith(("http://", "https://")):
        return _remote_rows(raw)
    return _local_rows(_local_path(raw))


def _remote_rows(url: str) -> list[Row]:
    try:
        _assert_public(url)
    except ValueError as exc:
        return _check([_meta("URL", _short(exc), "error")])
    links = reverse_links(url)
    try:
        data = _fetch(url)
    except Exception as exc:
        return _check([_meta("Fetch", _short(exc), "miss"), *links])
    return _check([*_inspect(data), *links])


def _local_rows(path: Path) -> list[Row]:
    if path.is_dir():
        return _check([_meta("File", "that path is a folder, not an image", "error")])
    if not path.is_file():
        return _check([_meta("File", "not found", "error")])
    try:
        size = path.stat().st_size
    except OSError as exc:
        return _check([_meta("File", _short(exc), "error")])
    if size > MAX_BYTES:
        return _check([_meta("File", "over 40 MB", "error"), _note()])
    try:
        data = path.read_bytes()
    except OSError as exc:
        return _check([_meta("File", _short(exc), "error")])
    return _check(_with_note(_inspect(data)))


def _note() -> Row:
    return _meta(
        "Reverse search",
        "This file stays on this machine. Reverse search needs a public URL.",
        "ready",
    )


def _with_note(rows: list[Row]) -> list[Row]:
    note = _note()
    for index, row in enumerate(rows):
        if row.category != "meta":
            return [*rows[:index], note, *rows[index:]]
    return [*rows, note]


def _inspect(data: bytes) -> list[Row]:
    digest = hashlib.sha256(data).hexdigest()
    try:
        found, gps = _read(data)
    except Exception as exc:
        return [
            _meta("SHA-256", digest),
            _meta("File size", _human_size(len(data))),
            _meta("Format", f"unreadable ({_short(exc)})", "error"),
        ]
    found.setdefault("SHA-256", digest)
    found.setdefault("File size", _human_size(len(data)))
    ordered = _order(found)
    rows: list[Row] = [_meta(name, value) for name, value in ordered]
    if not any(name not in {"Format", "Dimensions", "File size", "SHA-256"} for name, _value in ordered):
        rows.append(_meta("EXIF", "no EXIF or text metadata", "miss"))
    rows.extend(_map_rows(gps))
    rows.extend(_pivots(dict(ordered)))
    return rows


def _read(data: bytes) -> tuple[dict[str, str], tuple[float, float] | None]:
    from PIL import Image
    from PIL.ExifTags import TAGS

    found: dict[str, str] = {}
    with Image.open(BytesIO(data)) as image:
        image.load()
        if image.format:
            _put(found, "Format", image.format)
        width, height = image.size
        _put(found, "Dimensions", f"{width} x {height}")
        exif = image.getexif()
        if exif:
            _take(found, exif, TAGS)
            try:
                _take(found, exif.get_ifd(0x8769), TAGS)
            except Exception:
                pass
        for key, value in image.info.items():
            label = INFO_KEYS.get(str(key).lower())
            if label:
                _put(found, label, _text(value))
        gps = _gps(exif) if exif else None
    for name, value in _xmp(data).items():
        _put(found, XMP_ALIASES.get(name, name), value)
    if gps is not None:
        lat, lon, text = gps
        _put(found, "GPS", text)
        return found, (lat, lon)
    return found, None


def _take(found: dict[str, str], mapping, names: dict) -> None:
    for key, value in mapping.items():
        name = names.get(key)
        if name not in WANTED:
            continue
        _put(found, name, _text(value))


def _put(found: dict[str, str], name: str, value: str) -> None:
    text = _clean(value)
    if text and name not in found:
        found[name] = text


def _order(found: dict[str, str]) -> list[tuple[str, str]]:
    rank = {name: index for index, name in enumerate(PREFERRED)}
    names = sorted(found, key=lambda name: (rank.get(name, 1000), name))
    return [(name, found[name]) for name in names]


def _gps(exif) -> tuple[float, float, str] | None:
    try:
        gps = exif.get_ifd(0x8825)
    except Exception:
        return None
    if not gps:
        return None
    lat = _coord(gps.get(2), _text(gps.get(1)))
    lon = _coord(gps.get(4), _text(gps.get(3)))
    if lat is None or lon is None:
        return None
    text = f"{lat:.6f}, {lon:.6f}"
    altitude = gps.get(6)
    if altitude is not None:
        try:
            meters = _ratio(altitude)
            ref = gps.get(5)
            if ref in {1, b"\x01", "1"}:
                meters = -abs(meters)
            text += f"  alt {meters:.1f} m"
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    return lat, lon, text


def _coord(value, ref: str) -> float | None:
    if value is None:
        return None
    parts = list(value) if isinstance(value, (tuple, list)) else [value]
    try:
        nums = [_ratio(part) for part in parts]
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if len(nums) >= 3:
        degrees = nums[0] + nums[1] / 60.0 + nums[2] / 3600.0
    elif len(nums) == 1:
        degrees = nums[0]
    else:
        return None
    if ref.upper() in {"S", "W"} and degrees > 0:
        degrees = -degrees
    if abs(degrees) > 180:
        return None
    return degrees


def _ratio(value) -> float:
    if isinstance(value, tuple) and len(value) == 2 and not hasattr(value[0], "numerator"):
        denominator = value[1] or 1
        return float(value[0]) / float(denominator)
    if hasattr(value, "numerator") and hasattr(value, "denominator"):
        denominator = value.denominator or 1
        return float(value.numerator) / float(denominator)
    return float(value)


def _xmp(data: bytes) -> dict[str, str]:
    chunk = data[:262144]
    lowered = chunk.lower()
    if b"xmp" not in lowered and b"dc:creator" not in lowered:
        return {}
    text = chunk.decode("utf-8", errors="ignore")
    patterns = {
        "Creator": r"<dc:creator>.*?<rdf:li[^>]*>([^<]{1,200})</rdf:li>",
        "Rights": r"<dc:rights>.*?<rdf:li[^>]*>([^<]{1,200})</rdf:li>",
        "Description": r"<dc:description>.*?<rdf:li[^>]*>([^<]{1,300})</rdf:li>",
        "CreatorTool": r'xmp:CreatorTool="([^"]{1,200})"',
        "CreateDate": r'xmp:CreateDate="([^"]{1,80})"',
    }
    found: dict[str, str] = {}
    for name, pattern in patterns.items():
        match = re.search(pattern, text, re.I | re.S)
        if match:
            _put(found, name, _xml(match.group(1)))
    return found


def _xml(value: str) -> str:
    return (
        value.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
        .replace("&amp;", "&")
    )


def _pivots(found: dict[str, str]) -> list[Row]:
    rows: list[Row] = []
    seen: set[str] = set()

    def add(source: str, value: str) -> None:
        text = " ".join(value.replace('"', "").split())
        if len(text) < 3 or len(text) > 80:
            return
        url = "https://www.google.com/search?q=" + quote_plus(f'"{text}"')
        if url in seen:
            return
        seen.add(url)
        rows.append(_link(source, url, text, "web"))

    make = found.get("Make", "")
    model = found.get("Model", "")
    if make and model:
        add("Camera", f"{make} {model}")
    elif model or make:
        add("Camera", model or make)
    if found.get("LensModel"):
        add("Lens", found["LensModel"])
    for name in ("Artist", "Author", "Copyright", "Software", "Title"):
        if found.get(name):
            add(name, found[name])
    for name in ("BodySerialNumber", "LensSerialNumber", "ImageUniqueID"):
        if found.get(name):
            add("Serial", found[name])
    for name in ("Description", "ImageDescription", "UserComment", "Comment"):
        value = found.get(name, "")
        if len(value) >= 8:
            add("Text", value)
            break
    return rows


def _map_rows(gps: tuple[float, float] | None) -> list[Row]:
    if gps is None:
        return []
    lat, lon = gps
    coord = f"{lat:.6f},{lon:.6f}"
    shown = f"{lat:.6f}, {lon:.6f}"
    return [
        _link(
            "Google Maps",
            "https://www.google.com/maps/search/?api=1&query=" + quote_plus(coord),
            shown,
            "maps",
        ),
        _link(
            "OpenStreetMap",
            f"https://www.openstreetmap.org/?mlat={lat:.6f}&mlon={lon:.6f}#map=16/{lat:.6f}/{lon:.6f}",
            shown,
            "maps",
        ),
    ]


def _fetch(url: str) -> bytes:
    _assert_public(url)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept": "image/*,*/*;q=0.1"},
        method="GET",
    )
    opener = urllib.request.build_opener(_PublicRedirect)
    with opener.open(request, timeout=FETCH_TIMEOUT) as response:
        chunks: list[bytes] = []
        total = 0
        while True:
            block = response.read(65536)
            if not block:
                break
            total += len(block)
            if total > MAX_BYTES:
                raise ValueError("image is over 40 MB")
            chunks.append(block)
    data = b"".join(chunks)
    if not _magic(data):
        raise ValueError("response is not a recognized image")
    return data


class _PublicRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _assert_public(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _assert_public(url: str) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").strip("[]")
    if parsed.scheme not in {"http", "https"} or not host:
        raise ValueError("only an http(s) URL can be fetched")
    if parsed.username or parsed.password:
        raise ValueError("remove the username and password from the URL")
    lowered = host.lower()
    if lowered in {"localhost", "localhost.localdomain"} or lowered.endswith(".local"):
        raise ValueError("that address is not public")
    try:
        literal = ipaddress.ip_address(lowered)
    except ValueError:
        literal = None
    if literal is not None:
        if _blocked_ip(literal):
            raise ValueError("that address is not public")
        return
    previous = socket.getdefaulttimeout()
    socket.setdefaulttimeout(6)
    try:
        try:
            resolved = socket.getaddrinfo(host, parsed.port or 80, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise ValueError("could not resolve that host") from exc
    finally:
        socket.setdefaulttimeout(previous)
    addresses = []
    for item in resolved:
        try:
            addresses.append(ipaddress.ip_address(item[4][0].split("%")[0]))
        except ValueError:
            continue
    if not addresses or any(_blocked_ip(ip) for ip in addresses):
        raise ValueError("that address is not public")


def _blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _magic(data: bytes) -> bool:
    if data.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a", b"BM")):
        return True
    if data.startswith((b"II*\x00", b"MM\x00*")):
        return True
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return True
    return len(data) >= 12 and data[4:8] == b"ftyp"


def _local_path(raw: str) -> Path:
    if raw.lower().startswith("file:"):
        path = unquote(urlparse(raw).path)
        if re.match(r"^/[A-Za-z]:", path):
            path = path[1:]
        return Path(path)
    return Path(raw)


def _human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return _decode(value)
    if isinstance(value, str):
        return value.replace("\x00", " ")
    if hasattr(value, "numerator") and hasattr(value, "denominator"):
        denominator = value.denominator or 1
        numerator = value.numerator
        if denominator == 1:
            return str(int(numerator))
        if numerator == 1:
            return f"1/{int(denominator)}"
        return f"{float(numerator) / float(denominator):.4g}"
    if isinstance(value, (tuple, list)):
        parts = [_text(part) for part in value]
        return " ".join(part for part in parts if part)
    return str(value).replace("\x00", " ")


def _decode(value: bytes) -> str:
    if value.startswith(b"ASCII\x00\x00\x00"):
        return value[8:].decode("utf-8", errors="replace")
    if value.startswith(b"UNICODE\x00"):
        return value[8:].decode("utf-16le", errors="replace")
    if b"\x00" in value[:4] and len(value) % 2 == 0:
        text = value.decode("utf-16le", errors="ignore").replace("\x00", "").strip()
        if text and text.isprintable():
            return text
    return value.decode("utf-8", errors="replace")


def _clean(value: str) -> str:
    text = " ".join(str(value).split())
    if len(text) > 180:
        return text[:177] + "..."
    return text


def _short(exc: BaseException) -> str:
    text = " ".join(str(exc).split()) or type(exc).__name__
    return text[:120]


def _meta(source: str, detail: str, status: str = "hit") -> Row:
    hit = status == "hit"
    return Row(
        source=source,
        category="meta",
        tier="live" if hit else "free",
        status=status,
        detail=detail,
        url="",
        mode="probe" if hit else "direct",
    )


def _link(source: str, url: str, detail: str, category: str) -> Row:
    return Row(
        source=source,
        category=category,
        tier="free",
        status="ready",
        detail=detail,
        url=url,
        mode="direct",
    )


def _check(rows: list[Row]) -> list[Row]:
    for row in rows:
        if not row.url:
            if row.category != "meta":
                raise ValueError(f"missing url for {row.source}")
            continue
        if " " in row.url or not row.url.startswith("https://"):
            raise ValueError(f"bad url for {row.source}: {row.url}")
    return rows
