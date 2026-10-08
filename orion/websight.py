"""Read public contact details from one website.

WebSight fetches the page you name, plus a few contact or about pages on that
same site. It lists emails, phones, addresses, and names the site published.
It does not crawl the rest of the web, submit forms, or pass a login.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from html.parser import HTMLParser
from urllib.parse import quote_plus, urljoin, urlparse, urlunparse

from .image_meta import _PublicRedirect, _assert_public
from .models import STREET_RE, Card, Query, Row, phone_digits, phone_pretty

UA = "ORION-PublicSearch/1.0 (personal terminal; public page read)"
MAX_PAGES = 5
MAX_BYTES = 800_000
TIMEOUT = 8
CONTACT_WORDS = ("contact", "about", "team", "staff", "location", "support", "impressum", "connect")
ORG_TYPES = {"organization", "localbusiness", "store", "professionalservice", "homeandconstructionbusiness"}
SKIP_EMAIL_DOMAINS = {
    "example.com",
    "example.org",
    "example.net",
    "email.com",
    "domain.com",
    "sentry.io",
    "wixpress.com",
    "schema.org",
    "godaddy.com",
}
SKIP_LOCAL = {"noreply", "no-reply", "donotreply", "do-not-reply"}
EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,24}", re.I)
PHONE_RE = re.compile(r"(?:\+?1[\s.\-]?)?(?:\(\d{3}\)|\d{3})[\s.\-]\d{3}[\s.\-]\d{4}\b")


class _Bag:
    def __init__(self) -> None:
        self.company = ""
        self.emails: list[str] = []
        self.phones: list[str] = []
        self.addresses: list[str] = []
        self.names: list[str] = []
        self._email_keys: set[str] = set()
        self._phone_keys: set[str] = set()
        self._address_keys: set[str] = set()
        self._name_keys: set[str] = set()

    def add_email(self, value: str) -> None:
        for found in EMAIL_RE.findall(str(value or "")):
            email = found.strip(".").lower()
            local, _, domain = email.partition("@")
            if domain in SKIP_EMAIL_DOMAINS or local in SKIP_LOCAL or domain.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js")):
                continue
            if email in self._email_keys or len(self.emails) >= 8:
                continue
            self._email_keys.add(email)
            self.emails.append(email)

    def add_phone(self, value: str) -> None:
        digits = phone_digits(str(value or ""))
        if len(digits) not in {7, 10} or set(digits) <= {"0"}:
            return
        if digits in self._phone_keys or len(self.phones) >= 6:
            return
        self._phone_keys.add(digits)
        pretty = phone_pretty(digits)
        self.phones.append(pretty or digits)

    def add_address(self, value) -> None:
        line = _format_address(value)
        key = line.casefold()
        if not line or key in self._address_keys or len(self.addresses) >= 3:
            return
        self._address_keys.add(key)
        self.addresses.append(line)

    def add_name(self, value: str) -> None:
        name = " ".join(str(value or "").split())
        if not name or len(name) > 80 or name.casefold() == self.company.casefold():
            return
        key = name.casefold()
        if key in self._name_keys or len(self.names) >= 6:
            return
        if not re.search(r"[A-Za-z]", name):
            return
        self._name_keys.add(key)
        self.names.append(name)


def websight_rows(query: Query) -> list[Row]:
    try:
        start = _start_url(query.raw)
        _assert_public(start)
    except ValueError:
        return []
    host = _host(start)
    brand = host.split(".")[0].replace("-", " ")
    site = _row("Website", start, "open this site", "web")
    google = "https://www.google.com/search?q="
    rows = [
        site,
        _row("Google", google + quote_plus(f"site:{host}"), "pages on this site", "web"),
        _row("Google", google + quote_plus(f'"{host}"'), "mentions of this domain", "web"),
        _row("Bing", "https://www.bing.com/search?q=" + quote_plus(f"site:{host}"), "pages on this site", "web"),
        _row("DuckDuckGo", "https://duckduckgo.com/?q=" + quote_plus(f"site:{host}"), "pages on this site", "web"),
        _row(
            "LinkedIn",
            "https://www.linkedin.com/search/results/companies/?keywords=" + quote_plus(brand),
            "company search",
            "social",
        ),
        _row("crt.sh", "https://crt.sh/?q=" + quote_plus(host), "certificate search", "records"),
        _row("Whois", "https://www.whois.com/whois/" + quote_plus(host), "domain registration", "records"),
    ]
    return _check(rows)


def websight_cards(query: Query) -> list[Card]:
    try:
        start = _start_url(query.raw)
        _assert_public(start)
    except ValueError as exc:
        return [Card("WebSight", "Not read", [("NOTE", _short(exc))], "")]
    robots = _robots_text(start)
    bag = _Bag()
    seen: set[str] = set()
    queue = [start]
    homepage = _origin(start) + "/"
    if _clean(homepage) != _clean(start):
        queue.append(homepage)
    read = 0
    while queue and read < MAX_PAGES:
        url = queue.pop(0)
        key = _clean(url)
        if key in seen:
            continue
        seen.add(key)
        path = urlparse(url).path or "/"
        if robots and not _robots_allows(robots, path):
            continue
        try:
            ctype, html = _fetch(url)
        except Exception:
            continue
        if not _looks_html(ctype, html):
            continue
        read += 1
        page = _parse(html)
        _absorb(bag, page)
        if url == start:
            for extra in _discover(page.hrefs, start, robots):
                if _clean(extra) not in seen:
                    queue.append(extra)
    return [_card(start, bag, read)]


def parser_checks() -> list[tuple[str, bool]]:
    html = """
    <html><head>
    <title>Acme Roofing | Home</title>
    <meta property="og:site_name" content="Acme Roofing">
    <script type="application/ld+json">
    {"@type":"LocalBusiness","name":"Acme Roofing","email":"hello@acme-roofing.test",
     "telephone":"+1-415-555-0199",
     "address":{"@type":"PostalAddress","streetAddress":"10 Main St","addressLocality":"Austin","addressRegion":"TX","postalCode":"78701"},
     "employee":{"@type":"Person","name":"Ada Lovelace"}}
    </script>
    </head><body>
    <a href="mailto:sales@acme-roofing.test">Sales</a>
    <a href="/contact">Contact</a>
    <a href="https://other.example/about">Offsite</a>
    <p>Call (512) 555-0144</p>
    </body></html>
    """
    page = _parse(html)
    bag = _Bag()
    _absorb(bag, page)
    links = _discover(page.hrefs, "https://acme-roofing.test/", "")
    blocked = not _robots_allows("User-agent: *\nDisallow: /contact\n", "/contact")
    allowed = _robots_allows("User-agent: *\nDisallow: /private\nAllow: /contact\n", "/contact")
    return [
        ("websight company", bag.company == "Acme Roofing"),
        ("websight emails", set(bag.emails) == {"hello@acme-roofing.test", "sales@acme-roofing.test"}),
        ("websight phones", "(415) 555-0199" in bag.phones and "(512) 555-0144" in bag.phones),
        ("websight address", any("10 Main St" in line and "78701" in line for line in bag.addresses)),
        ("websight name", "Ada Lovelace" in bag.names),
        ("websight same site", any(link.endswith("/contact") and "acme-roofing.test" in link for link in links)),
        ("websight skips offsite", all("other.example" not in link for link in links)),
        ("websight robots block", blocked and allowed),
    ]


def _card(start: str, bag: _Bag, read: int) -> Card:
    host = _host(start) or start
    title = bag.company or host
    fields: list[tuple[str, str]] = []
    if bag.company:
        fields.append(("COMPANY", bag.company))
    fields.extend(("EMAIL", email) for email in bag.emails)
    fields.extend(("PHONE", phone) for phone in bag.phones)
    fields.extend(("ADDRESS", address) for address in bag.addresses)
    fields.extend(("NAME", name) for name in bag.names)
    if read == 0:
        fields.append(("NOTE", "No public page could be read."))
    elif not (bag.emails or bag.phones or bag.addresses or bag.names):
        fields.append(("NOTE", f"No email, phone, or address on {read} page{'s' if read != 1 else ''} read."))
    return Card(
        "WebSight",
        title,
        fields,
        start,
        "Published on this site. Read from the pages ORION was allowed to open.",
    )


def _absorb(bag: _Bag, page: "_Page") -> None:
    if page.company and not bag.company:
        bag.company = page.company
    for value in page.emails:
        bag.add_email(value)
    for value in page.phones:
        bag.add_phone(value)
    for value in page.addresses:
        bag.add_address(value)
    for value in page.names:
        bag.add_name(value)
    for blob in page.jsonld:
        _walk_json(blob, bag)
    if page.company and not bag.company:
        bag.company = page.company


class _Page:
    def __init__(self) -> None:
        self.company = ""
        self.hrefs: list[str] = []
        self.emails: list[str] = []
        self.phones: list[str] = []
        self.addresses: list[str] = []
        self.names: list[str] = []
        self.jsonld: list[str] = []


def _parse(html: str) -> _Page:
    page = _Page()
    parser = _Collector()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        parser.text = html
    page.hrefs = parser.hrefs
    page.jsonld = parser.jsonld
    page.company = _clean_title(parser.og or parser.title)
    page.emails = EMAIL_RE.findall(parser.text)
    for href in parser.hrefs:
        if href.lower().startswith("mailto:"):
            page.emails.append(href.split(":", 1)[1].split("?", 1)[0])
        if href.lower().startswith("tel:"):
            page.phones.append(href.split(":", 1)[1])
    page.phones.extend(PHONE_RE.findall(parser.text))
    for block in parser.addresses:
        if _looks_address(block):
            page.addresses.append(block)
    return page


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []
        self.title = ""
        self.og = ""
        self.text_parts: list[str] = []
        self.addresses: list[str] = []
        self.jsonld: list[str] = []
        self._skip = 0
        self._title: list[str] = []
        self._in_title = False
        self._address: list[str] = []
        self._in_address = 0
        self._json: list[str] | None = None

    @property
    def text(self) -> str:
        return " ".join(self.text_parts)

    @text.setter
    def text(self, value: str) -> None:
        self.text_parts = [value]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key.lower(): value or "" for key, value in attrs}
        if tag in {"script", "style", "noscript"}:
            self._skip += 1
            if tag == "script" and "ld+json" in attr.get("type", "").lower():
                self._json = []
        if tag == "a" and attr.get("href"):
            self.hrefs.append(attr["href"].strip())
        if tag == "title":
            self._in_title = True
        if tag == "address":
            self._in_address += 1
        if tag == "meta":
            prop = (attr.get("property") or attr.get("name") or "").lower()
            if prop in {"og:site_name", "og:title"} and attr.get("content") and not self.og:
                self.og = attr["content"]

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._json is not None:
            self.jsonld.append("".join(self._json))
            self._json = None
        if tag in {"script", "style", "noscript"} and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
            self.title = " ".join(self._title)
        if tag == "address" and self._in_address:
            self._in_address -= 1
            text = " ".join(self._address)
            self._address = []
            if text.strip():
                self.addresses.append(" ".join(text.split()))

    def handle_data(self, data: str) -> None:
        if self._json is not None:
            self._json.append(data)
            return
        if self._skip:
            return
        if self._in_title:
            self._title.append(data)
        if self._in_address:
            self._address.append(data)
        if data.strip():
            self.text_parts.append(data.strip())


def _walk_json(blob: str, bag: _Bag) -> None:
    try:
        payload = json.loads(blob)
    except Exception:
        return
    _walk(payload, bag)


def _walk(node, bag: _Bag) -> None:
    if isinstance(node, list):
        for item in node:
            _walk(item, bag)
        return
    if not isinstance(node, dict):
        return
    types = {str(item).lower() for item in _as_list(node.get("@type"))}
    name = node.get("name")
    if "person" in types and isinstance(name, str):
        bag.add_name(name)
    if types & ORG_TYPES and isinstance(name, str) and not bag.company:
        bag.company = " ".join(name.split())
    email = node.get("email")
    if isinstance(email, str):
        bag.add_email(email)
    elif isinstance(email, list):
        for item in email:
            bag.add_email(str(item))
    phone = node.get("telephone")
    if isinstance(phone, str):
        bag.add_phone(phone)
    elif isinstance(phone, list):
        for item in phone:
            bag.add_phone(str(item))
    if "address" in node:
        bag.add_address(node.get("address"))
    for key in ("employee", "employees", "founder", "contactPoint", "member", "@graph"):
        if key in node:
            _walk(node[key], bag)


def _format_address(value) -> str:
    if isinstance(value, list):
        for item in value:
            line = _format_address(item)
            if line:
                return line
        return ""
    if isinstance(value, str):
        text = " ".join(value.split())
        return text if _looks_address(text) else ""
    if not isinstance(value, dict):
        return ""
    street = " ".join(str(value.get("streetAddress") or "").split())
    city = " ".join(str(value.get("addressLocality") or "").split())
    region = " ".join(str(value.get("addressRegion") or "").split())
    postal = " ".join(str(value.get("postalCode") or "").split())
    tail = " ".join(part for part in (region, postal) if part)
    place = ", ".join(part for part in (city, tail) if part)
    return ", ".join(part for part in (street, place) if part)


def _looks_address(text: str) -> bool:
    return bool(STREET_RE.search(text) or re.search(r"\b\d{5}(?:-\d{4})?\b", text))


def _discover(hrefs: list[str], start: str, robots: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    def add(url: str) -> None:
        cleaned = _clean(url)
        parsed = urlparse(cleaned)
        if parsed.scheme not in {"http", "https"} or _host(cleaned) != _host(start):
            return
        path = (parsed.path or "/").lower()
        if path.endswith((".pdf", ".jpg", ".jpeg", ".png", ".gif", ".zip", ".css", ".js")):
            return
        if robots and not _robots_allows(robots, parsed.path or "/"):
            return
        if cleaned in seen:
            return
        seen.add(cleaned)
        found.append(cleaned)

    for href in hrefs:
        absolute = urljoin(start, href.strip())
        path = urlparse(absolute).path.lower()
        if any(word in path for word in CONTACT_WORDS):
            add(absolute)
    origin = _origin(start)
    for suffix in ("/contact", "/contact-us", "/about", "/about-us"):
        add(origin + suffix)
    return found[: MAX_PAGES - 1]


def _robots_text(start: str) -> str:
    url = _origin(start) + "/robots.txt"
    try:
        _ctype, text = _fetch(url)
    except Exception:
        return ""
    if "user-agent" not in text.lower():
        return ""
    return text


def _robots_allows(text: str, path: str) -> bool:
    if not path.startswith("/"):
        path = "/" + path
    best = -1
    allowed = True
    for allow, rule in _robots_rules(text):
        if rule.endswith("$"):
            literal = rule[:-1]
            matched = path == literal
            length = len(literal)
        else:
            matched = path.startswith(rule)
            length = len(rule)
        if matched and length >= best:
            best = length
            allowed = allow
    return allowed


def _robots_rules(text: str) -> list[tuple[bool, str]]:
    agents: list[str] = []
    rules: list[tuple[bool, str]] = []
    star: list[tuple[bool, str]] = []
    seen_rule = False

    def close() -> None:
        nonlocal agents, rules, seen_rule
        if any(agent == "*" for agent in agents):
            star.extend(rules)
        agents = []
        rules = []
        seen_rule = False

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            if seen_rule:
                close()
            agents.append(value.lower())
        elif key in {"allow", "disallow"}:
            seen_rule = True
            rule = value.split("*", 1)[0]
            if rule:
                rules.append((key == "allow", rule))
    close()
    return star


def _fetch(url: str) -> tuple[str, str]:
    _assert_public(url)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1"},
        method="GET",
    )
    opener = urllib.request.build_opener(_PublicRedirect)
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            ctype = response.headers.get("Content-Type", "")
            data = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise ValueError(f"http {exc.code}") from exc
    if len(data) > MAX_BYTES:
        data = data[:MAX_BYTES]
    match = re.search(r"charset=([A-Za-z0-9._\-]+)", ctype, re.I)
    charset = match.group(1) if match else "utf-8"
    return ctype, data.decode(charset, errors="replace")


def _looks_html(ctype: str, html: str) -> bool:
    lowered = ctype.lower()
    if "html" in lowered or "xml" in lowered:
        return True
    sample = html.lstrip().lower()[:800]
    return any(token in sample for token in ("<!doctype", "<html", "<head", "<title", "<body", "<meta"))


def _start_url(raw: str) -> str:
    text = raw.strip()
    if not re.match(r"https?://", text, re.I):
        text = "https://" + text
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("enter a public web address")
    path = parsed.path or "/"
    return urlunparse(("https", parsed.netloc, path, "", parsed.query, ""))


def _origin(url: str) -> str:
    parsed = urlparse(url)
    return f"https://{parsed.netloc}"


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _clean(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    return urlunparse(("https", (parsed.netloc or "").lower(), path, "", parsed.query, ""))


def _clean_title(value: str) -> str:
    text = " ".join(value.split())
    generic = {"home", "welcome", "contact", "about", "about us"}
    for mark in (" | ", " - ", " – ", " — "):
        if mark in text:
            parts = [part.strip() for part in text.split(mark) if part.strip()]
            named = [part for part in parts if part.casefold() not in generic]
            if named:
                text = max(named, key=len)
            elif parts:
                text = parts[0]
            break
    return text[:80]


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _short(exc: BaseException) -> str:
    text = " ".join(str(exc).split()) or type(exc).__name__
    return text[:120]


def _row(source: str, url: str, detail: str, category: str) -> Row:
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
        if " " in row.url or not row.url.startswith("https://"):
            raise ValueError(f"bad url for {row.source}: {row.url}")
    return rows
