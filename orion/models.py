"""Query parsing and result rows."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from math import log2
from pathlib import Path
from urllib.parse import unquote, urlparse

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
HANDLE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,38}")
STREET_RE = re.compile(
    r"\b(st|street|ave|avenue|rd|road|blvd|boulevard|ln|lane|dr|drive|"
    r"ct|court|way|pl|place|ter|terrace|cir|circle|hwy|highway)\b",
    re.I,
)
WEB_TLDS = frozenset(
    {
        "com",
        "org",
        "net",
        "io",
        "co",
        "us",
        "uk",
        "ca",
        "au",
        "de",
        "fr",
        "app",
        "dev",
        "info",
        "biz",
        "me",
        "tv",
        "cc",
        "gov",
        "edu",
        "mil",
        "ai",
        "xyz",
        "site",
        "online",
        "shop",
        "pro",
        "name",
        "mobi",
        "ly",
        "to",
        "fm",
        "gg",
    }
)

IMAGE_SUFFIXES = {
    ".jpg",
    ".jpeg",
    ".jfif",
    ".png",
    ".webp",
    ".tif",
    ".tiff",
    ".gif",
    ".heic",
    ".bmp",
}

STATES: dict[str, str] = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "DC": "District of Columbia",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
}

CAT_ORDER = ("meta", "core", "people", "social", "web", "records", "maps", "live")

CAT_LABELS = {
    "meta": "IMAGE METADATA",
    "core": "CORE MESH",
    "people": "MORE DIRECTORIES",
    "social": "SOCIAL PROFILES",
    "web": "WEB ENGINES",
    "records": "PUBLIC RECORDS",
    "maps": "MAPS",
    "live": "LIVE CHECKS",
}

CORE_SOURCES = (
    "ThatsThem",
    "Whitepages",
    "Intelius",
    "TruePeopleSearch",
    "ZabaSearch",
    "PeekYou",
    "TruthFinder",
    "US Search",
    "Spokeo",
    "Google",
    "LinkedIn",
)


@dataclass
class Query:
    kind: str
    raw: str
    city: str = ""
    state: str = ""
    postal: str = ""

    def __post_init__(self) -> None:
        text = _unwrap(self.raw)
        self.raw = text if self.kind in {"image", "websight"} else " ".join(text.split())
        self.city = tidy_city(self.city)
        code, name = split_state(self.state)
        self.state = code or name
        self.state_name = name or code
        self.postal = self.postal.strip()

    @property
    def location(self) -> str:
        parts = [self.city, self.state, self.postal]
        return " ".join(part for part in parts if part)

    @property
    def citystatezip(self) -> str:
        tail = " ".join(part for part in (self.state, self.postal) if part)
        if self.city and tail:
            return f"{self.city}, {tail}"
        return " ".join(part for part in (self.city, self.state, self.postal) if part)

    @property
    def needle(self) -> str:
        if self.kind in {"name", "address"} and self.location:
            return f"{self.raw} {self.location}".strip()
        return self.raw

    def facts(self) -> list[tuple[str, str]]:
        facts = [("TYPE", self.kind.upper()), ("QUERY", self.raw)]
        if self.location:
            facts.append(("PLACE", self.location))
        if self.kind == "name":
            first, _middle, last = split_name(self.raw)
            facts.append(("TOKENS", str(len(self.raw.split()))))
            if first:
                facts.append(("FIRST", first))
            if last:
                facts.append(("LAST", last))
        elif self.kind == "username":
            handle = clean_handle(self.raw)
            facts.append(("HANDLE", handle))
            facts.append(("LENGTH", str(len(handle))))
            facts.append(("ENTROPY", f"{shannon(handle):.2f} b/ch"))
        elif self.kind == "email":
            local, domain = email_parts(self.raw)
            facts.append(("LOCAL", local))
            facts.append(("DOMAIN", domain))
        elif self.kind == "phone":
            digits = phone_digits(self.raw)
            pretty = phone_pretty(digits)
            if pretty:
                facts.append(("FORMAT", pretty))
            if len(digits) == 10:
                facts.append(("NPA", digits[:3]))
                facts.append(("NXX", digits[3:6]))
            facts.append(("DIGITS", str(len(digits))))
        elif self.kind == "address":
            facts.append(("NUMBER", "yes" if re.match(r"\d", self.raw) else "no"))
            facts.append(("STREET WORD", "yes" if STREET_RE.search(self.raw) else "no"))
        elif self.kind == "image":
            remote = self.raw.lower().startswith(("http://", "https://"))
            facts.append(("TARGET", "url" if remote else "file"))
            name = Path(urlparse(self.raw).path).name if remote else Path(self.raw).name
            if name:
                facts.append(("NAME", name))
        elif self.kind == "websight":
            target = self.raw if "://" in self.raw else "https://" + self.raw
            host = (urlparse(target).hostname or "").lower().removeprefix("www.")
            if host:
                facts.append(("HOST", host))
        return facts


@dataclass
class Row:
    source: str
    category: str
    tier: str
    status: str
    detail: str
    url: str
    mode: str
    core: bool = False
    elapsed_ms: int | None = None


@dataclass
class Card:
    source: str
    title: str
    fields: list[tuple[str, str]]
    url: str
    note: str = ""


@dataclass
class Result:
    query: Query
    rows: list[Row]
    elapsed_s: float
    tag: str
    feed: list[str] = field(default_factory=list)
    cards: list[Card] = field(default_factory=list)
    preview: bool = False

    def visible(self) -> list[Row]:
        return [row for row in self.rows if row.status != "skip"]

    def probes(self) -> list[Row]:
        return [row for row in self.rows if row.category == "live" and row.status != "skip"]

    def hits(self) -> list[Row]:
        return [row for row in self.rows if row.status == "hit"]


@dataclass
class Session:
    started: float
    searches: int = 0
    links: int = 0
    hits: int = 0
    last: Result | None = None
    history: list[str] = field(default_factory=list)
    hit_track: list[int] = field(default_factory=list)

    def remember(self, result: Result) -> None:
        self.searches += 1
        visible = result.visible()
        link_count = sum(1 for row in visible if row.category != "live" and row.url)
        hit_count = len(result.hits())
        self.links += link_count
        self.hits += hit_count
        self.hit_track.append(hit_count)
        self.last = result
        place = f"  {result.query.location}" if result.query.location else ""
        self.history.append(
            f"{result.query.kind.upper():<8}  {result.query.raw}{place}   "
            f"{link_count} links   {hit_count} hits"
        )
        self.history = self.history[-6:]


def _unwrap(raw: str) -> str:
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1].strip()
    return text


def is_image_target(raw: str) -> bool:
    text = _unwrap(raw)
    lowered = text.lower()
    if lowered.startswith(("http://", "https://", "file:")):
        return Path(unquote(urlparse(text).path)).suffix.lower() in IMAGE_SUFFIXES
    return Path(text).suffix.lower() in IMAGE_SUFFIXES


def is_web_target(raw: str) -> bool:
    text = _unwrap(raw)
    if not text or " " in text or "@" in text:
        return False
    candidate = re.sub(r"^https?://", "", text, count=1, flags=re.I)
    host = candidate.split("/")[0].split("?")[0].split("#")[0]
    if "@" in host:
        return False
    if host.startswith("[") and "]" in host:
        return False
    host = host.removeprefix("www.")
    if ":" in host:
        host = host.rsplit(":", 1)[0]
    labels = host.lower().strip(".").split(".")
    if len(labels) < 2 or labels[-1] not in WEB_TLDS:
        return False
    return all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)


def tidy_city(city: str) -> str:
    city = " ".join(city.strip().split())
    if city.islower():
        return city.title()
    return city


def split_state(value: str) -> tuple[str, str]:
    text = value.strip()
    if not text:
        return "", ""
    key = re.sub(r"[^A-Za-z]", "", text).upper()
    if key in STATES:
        return key, STATES[key]
    for code, name in STATES.items():
        if name.upper() == text.upper():
            return code, name
    if len(text) <= 3:
        return text.upper(), text.upper()
    return "", text


def split_name(name: str) -> tuple[str, str, str]:
    parts = name.split()
    if not parts:
        return "", "", ""
    if len(parts) == 1:
        return "", "", parts[0]
    if len(parts) == 2:
        return parts[0], "", parts[1]
    return parts[0], " ".join(parts[1:-1]), parts[-1]


def slug(text: str, sep: str = "-", lower: bool = False) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    if lower:
        text = text.lower()
    text = re.sub(r"[^A-Za-z0-9]+", sep, text).strip(sep)
    return text


def phone_digits(raw: str) -> str:
    trimmed = re.sub(r"(?:ext\.?|extension|x)\s*\d+\s*$", "", raw.strip(), flags=re.I)
    digits = re.sub(r"\D", "", trimmed)
    if len(digits) == 11 and digits.startswith("1"):
        return digits[1:]
    return digits


def phone_pretty(digits: str) -> str:
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    if len(digits) == 7:
        return f"{digits[:3]}-{digits[3:]}"
    return digits


def phone_dashed(digits: str) -> str:
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    if len(digits) == 7:
        return f"{digits[:3]}-{digits[3:]}"
    return digits


def email_parts(email: str) -> tuple[str, str]:
    local, _, domain = email.strip().partition("@")
    return local, domain.lower()


def clean_handle(raw: str) -> str:
    handle = raw.strip()
    if handle.startswith("@"):
        handle = handle[1:]
    return handle.strip()


def handle_ok(handle: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,38}", handle))


def shannon(text: str) -> float:
    if not text:
        return 0.0
    counts = Counter(text)
    total = len(text)
    return -sum((count / total) * log2(count / total) for count in counts.values())


def detect(raw: str) -> str:
    text = _unwrap(raw)
    if is_image_target(text):
        return "image"
    if is_web_target(text):
        return "websight"
    text = " ".join(text.split())
    if EMAIL_RE.match(text):
        return "email"
    phone_text = re.sub(r"(?:ext\.?|extension|x)\s*\d+\s*$", "", text, flags=re.I)
    digits = re.sub(r"\D", "", phone_text)
    letters = re.sub(r"[^A-Za-z]", "", phone_text)
    phone_marks = set("0123456789+().- \t")
    if len(digits) >= 7 and not letters and all(ch in phone_marks for ch in phone_text):
        return "phone"
    if STREET_RE.search(text) or re.match(r"\d+\s+\S+", text):
        return "address"
    if " " not in text:
        handle = clean_handle(text)
        if handle_ok(handle) and not (handle.isalpha() and handle[:1].isupper() and handle[1:].islower()):
            if not (handle.isalpha() and handle.isupper() and len(handle) > 1):
                return "username"
    return "name"


def query_tag(query: Query) -> str:
    payload = "|".join((query.kind, query.raw, query.city, query.state, query.postal))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12].upper()


def valid_email(value: str) -> bool:
    return bool(EMAIL_RE.match(value.strip()))
