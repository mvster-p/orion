"""Short public previews for the summary panel.

These calls use public JSON APIs. People-search directory pages are not fetched.
A returned record is a possible public match, not proof of identity.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from collections.abc import Callable
from urllib.parse import quote_plus, urlencode

from .models import Card, Query, clean_handle, handle_ok, phone_pretty, split_name, valid_email
from .probe import fetch

CardJob = tuple[str, Callable[[], list[Card]]]

_NPI_NOTE = "Health-provider registry record. Not proof this is the person you searched."
_MAP_NOTE = "Normalized public address. No resident names."


def card_jobs(query: Query) -> list[CardJob]:
    if query.kind == "name" and query.raw.strip():
        jobs: list[CardJob] = [
            ("Wikipedia", lambda query=query: _wikipedia(query)),
            ("GitHub", lambda query=query: _github_name(query)),
        ]
        first, _middle, last = split_name(query.raw)
        if first and last:
            jobs.append(("NPI Registry", lambda query=query: _npi(query)))
        return jobs
    if query.kind == "username":
        handle = clean_handle(query.raw)
        if handle_ok(handle):
            return [("GitHub", lambda handle=handle: _github_login(handle))]
    if query.kind == "email" and valid_email(query.raw):
        return [("Gravatar", lambda query=query: _gravatar(query.raw))]
    if query.kind == "address" and query.raw.strip():
        return [("OpenStreetMap", lambda query=query: _nominatim(query))]
    if query.kind == "websight" and query.raw.strip():
        from .websight import websight_cards

        return [("WebSight", lambda query=query: websight_cards(query))]
    return []


def run_cards(fn: Callable[[], list[Card]]) -> list[Card]:
    try:
        found = fn()
    except Exception:
        return []
    if not found:
        return []
    return [
        card
        for card in found
        if isinstance(card, Card) and card.title and (not card.url or card.url.startswith("https://"))
    ]


def order_cards(cards: list[Card]) -> list[Card]:
    rank = {"WebSight": 0, "Wikipedia": 1, "GitHub": 2, "Gravatar": 3, "NPI Registry": 4, "OpenStreetMap": 5}
    return sorted(cards, key=lambda card: (rank.get(card.source, 50), card.title.lower()))


def parser_checks() -> list[tuple[str, bool]]:
    wiki = _wikipedia_card(
        "Grace Hopper",
        [
            "Grace Hopper",
            ["Grace Hopper", "Grace Hopper (disambiguation)"],
            ["American computer scientist and Navy rear admiral.", "Topics named Grace Hopper."],
            [
                "https://en.wikipedia.org/wiki/Grace_Hopper",
                "https://en.wikipedia.org/wiki/Grace_Hopper_(disambiguation)",
            ],
        ],
    )
    skipped = _wikipedia_card(
        "John Smith",
        ["John Smith", ["John (disambiguation)"], ["John may refer to:"], ["https://en.wikipedia.org/wiki/John"]],
    )
    profile = {
        "login": "ghopper",
        "name": "Grace Hopper",
        "company": "US Navy",
        "location": "Arlington, VA",
        "blog": "https://example.com/hopper",
        "bio": "Compiler work.",
        "public_repos": 2,
        "html_url": "https://github.com/ghopper",
    }
    matched = _github_card(profile, "Grace Hopper")
    rejected = _github_card(profile, "Ada Lovelace")
    npi = _npi_cards(
        {
            "result_count": 4,
            "results": [
                {
                    "number": "1234567893",
                    "basic": {"first_name": "GRACE", "last_name": "HOPPER", "credential": "M.D."},
                    "addresses": [
                        {
                            "address_purpose": "LOCATION",
                            "address_1": "1 MAIN ST",
                            "city": "ARLINGTON",
                            "state": "VA",
                            "postal_code": "222011234",
                            "telephone_number": "703-555-0134",
                        }
                    ],
                    "taxonomies": [{"desc": "Internal Medicine", "primary": True}],
                }
            ],
        },
        "Grace",
        "Hopper",
    )
    place = _nominatim_card(
        [
            {
                "display_name": "1600, Pennsylvania Avenue Northwest, Washington, D.C.",
                "lat": "38.897700",
                "lon": "-77.036500",
                "address": {
                    "house_number": "1600",
                    "road": "Pennsylvania Avenue Northwest",
                    "city": "Washington",
                    "state": "District of Columbia",
                    "postcode": "20500",
                },
            }
        ]
    )
    mail = _gravatar_card(
        {
            "entry": [
                {
                    "displayName": "Ada",
                    "profileUrl": "http://gravatar.com/ada",
                    "currentLocation": "London",
                    "aboutMe": "Mathematician.",
                    "name": {"formatted": "Ada Lovelace"},
                    "phoneNumbers": [{"value": "+1-415-555-0134"}],
                    "urls": [{"value": "https://example.com/ada", "title": "Site"}],
                }
            ]
        }
    )
    phone = dict(npi[0].fields).get("PHONE", "") if npi else ""
    address = dict(npi[0].fields).get("ADDRESS", "") if npi else ""
    return [
        ("wiki summary", wiki is not None and wiki.title == "Grace Hopper" and "scientist" in wiki.fields[0][1]),
        ("wiki skips disambiguation", skipped is None),
        ("github name match", matched is not None and dict(matched.fields).get("LOCATION") == "Arlington, VA"),
        ("github rejects other name", rejected is None),
        ("npi phone", phone == "(703) 555-0134"),
        ("npi address", "1 MAIN ST" in address and "22201-1234" in address),
        ("nominatim address", place is not None and "1600" in dict(place.fields).get("ADDRESS", "")),
        ("gravatar profile", mail is not None and mail.url.startswith("https://") and "Lovelace" in mail.title),
    ]


def _wikipedia(query: Query) -> list[Card]:
    url = (
        "https://en.wikipedia.org/w/api.php?action=opensearch&limit=3&namespace=0&format=json&search="
        + quote_plus(query.raw)
    )
    status, body, _error = fetch(url)
    if status != 200:
        return []
    card = _wikipedia_card(query.raw, _json(body))
    if card is None:
        return []
    extract = _wiki_extract(card.title)
    if extract:
        card.fields = [("BIO", extract)]
    return [card]


def _wikipedia_card(query: str, payload) -> Card | None:
    if not isinstance(payload, list) or len(payload) < 4:
        return None
    titles, blurbs, urls = payload[1], payload[2], payload[3]
    if not isinstance(titles, list) or not isinstance(blurbs, list) or not isinstance(urls, list):
        return None
    for title, blurb, url in zip(titles, blurbs, urls):
        if not isinstance(title, str) or not isinstance(url, str) or not url.startswith("https://"):
            continue
        lowered = title.lower()
        text = _plain(blurb if isinstance(blurb, str) else "")
        if "disambiguation" in lowered or text.lower().startswith("may refer to") or "may refer to:" in text.lower():
            continue
        if not _title_ok(query, title):
            continue
        fields = [("BIO", text)] if text else []
        return Card("Wikipedia", title, fields, url)
    return None


def _wiki_extract(title: str) -> str:
    from urllib.parse import quote

    status, body, _error = fetch(
        "https://en.wikipedia.org/api/rest_v1/page/summary/" + quote(title.replace(" ", "_"), safe="")
    )
    if status != 200:
        return ""
    data = _json(body)
    if not isinstance(data, dict) or data.get("type") == "disambiguation":
        return ""
    return _clip(_plain(data.get("extract") or ""), 360)


def _github_name(query: Query) -> list[Card]:
    status, body, _error = fetch(
        "https://api.github.com/search/users?per_page=2&q=" + quote_plus(f'fullname:"{query.raw}"')
    )
    if status != 200:
        return []
    data = _json(body)
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    for item in items[:2]:
        if not isinstance(item, dict):
            continue
        login = str(item.get("login") or "")
        found = _github_login(login, query.raw)
        if found and any(label in {"COMPANY", "LOCATION", "SITE", "BIO"} for label, _value in found[0].fields):
            return found
    return []


def _github_login(login: str, expected_name: str = "") -> list[Card]:
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", login):
        return []
    status, body, _error = fetch(f"https://api.github.com/users/{login}")
    if status != 200:
        return []
    data = _json(body)
    if not isinstance(data, dict):
        return []
    card = _github_card(data, expected_name)
    return [card] if card else []


def _github_card(profile: dict, expected_name: str) -> Card | None:
    name = _plain(profile.get("name") or "")
    login = _plain(profile.get("login") or "")
    if expected_name and not _person_match(expected_name, name):
        return None
    url = str(profile.get("html_url") or "")
    if not url.startswith("https://github.com/"):
        return None
    fields: list[tuple[str, str]] = []
    company = _plain(profile.get("company") or "").lstrip("@")
    location = _plain(profile.get("location") or "")
    blog = _plain(profile.get("blog") or "")
    bio = _plain(profile.get("bio") or "")
    repos = profile.get("public_repos")
    if company:
        fields.append(("COMPANY", company))
    if location:
        fields.append(("LOCATION", location))
    if blog.startswith("http://") or blog.startswith("https://"):
        fields.append(("SITE", blog))
    if bio:
        fields.append(("BIO", _clip(bio, 180)))
    if isinstance(repos, int):
        fields.append(("REPOS", str(repos)))
    if not name and not fields:
        return None
    return Card("GitHub", name or login, fields, url)


def _npi(query: Query) -> list[Card]:
    first, _middle, last = split_name(query.raw)
    if not first or not last:
        return []
    params = {
        "version": "2.1",
        "first_name": first,
        "last_name": last,
        "limit": "5",
    }
    if len(query.state) == 2 and query.state.isalpha():
        params["state"] = query.state.upper()
    if query.city:
        params["city"] = query.city
    status, body, _error = fetch("https://npiregistry.cms.hhs.gov/api/?" + urlencode(params))
    if status != 200:
        return []
    data = _json(body)
    if not isinstance(data, dict):
        return []
    return _npi_cards(data, first, last)


def _npi_cards(payload: dict, first: str, last: str) -> list[Card]:
    results = payload.get("results")
    if not isinstance(results, list):
        return []
    cards: list[Card] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        basic = item.get("basic") if isinstance(item.get("basic"), dict) else {}
        if str(basic.get("first_name") or "").casefold() != first.casefold():
            continue
        if str(basic.get("last_name") or "").casefold() != last.casefold():
            continue
        number = re.sub(r"\D", "", str(item.get("number") or ""))
        if len(number) != 10:
            continue
        title = " ".join(part for part in (str(basic.get("first_name") or ""), str(basic.get("last_name") or "")) if part)
        title = title.title()
        credential = _plain(basic.get("credential") or "")
        if credential:
            title = f"{title}, {credential}"
        address = _npi_address(item.get("addresses"))
        fields = [("NAME", title)]
        if address["phone"]:
            fields.append(("PHONE", address["phone"]))
        if address["address"]:
            fields.append(("ADDRESS", address["address"]))
        role = _primary_role(item.get("taxonomies"))
        if role:
            fields.append(("ROLE", role))
        fields.append(("NPI", number))
        cards.append(
            Card(
                "NPI Registry",
                title,
                fields,
                f"https://npiregistry.cms.hhs.gov/provider-view/{number}",
                _NPI_NOTE,
            )
        )
        if len(cards) == 2:
            break
    count = payload.get("result_count")
    if cards and isinstance(count, int) and count > len(cards):
        cards[0].note = f"{count} registry records. Showing {len(cards)}. " + cards[0].note
    return cards


def _npi_address(addresses) -> dict[str, str]:
    chosen = {}
    if isinstance(addresses, list):
        for item in addresses:
            if isinstance(item, dict) and item.get("address_purpose") == "LOCATION":
                chosen = item
                break
        if not chosen:
            chosen = next((item for item in addresses if isinstance(item, dict)), {})
    street = ", ".join(_plain(chosen.get(key) or "") for key in ("address_1", "address_2"))
    street = ", ".join(part for part in street.split(", ") if part)
    postal = re.sub(r"\D", "", str(chosen.get("postal_code") or ""))
    if len(postal) == 9:
        postal = f"{postal[:5]}-{postal[5:]}"
    elif len(postal) > 5:
        postal = postal[:5]
    place = " ".join(
        part
        for part in (
            _plain(chosen.get("city") or "").title(),
            _plain(chosen.get("state") or "").upper(),
            postal,
        )
        if part
    )
    line = ", ".join(part for part in (street, place) if part)
    digits = re.sub(r"\D", "", str(chosen.get("telephone_number") or ""))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    phone = phone_pretty(digits) if len(digits) in {7, 10} else ""
    if set(digits) == {"0"}:
        phone = ""
    return {"phone": phone, "address": line}


def _primary_role(taxonomies) -> str:
    if not isinstance(taxonomies, list):
        return ""
    chosen = next((item for item in taxonomies if isinstance(item, dict) and item.get("primary")), None)
    if chosen is None:
        chosen = next((item for item in taxonomies if isinstance(item, dict)), None)
    if not isinstance(chosen, dict):
        return ""
    return _plain(chosen.get("desc") or "")


def _nominatim(query: Query) -> list[Card]:
    params = urlencode({"q": query.needle, "format": "jsonv2", "addressdetails": 1, "limit": 1})
    status, body, _error = fetch("https://nominatim.openstreetmap.org/search?" + params)
    if status != 200:
        return []
    card = _nominatim_card(_json(body))
    return [card] if card else []


def _nominatim_card(payload) -> Card | None:
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        return None
    item = payload[0]
    try:
        lat = float(item.get("lat"))
        lon = float(item.get("lon"))
    except (TypeError, ValueError):
        return None
    if abs(lat) > 90 or abs(lon) > 180:
        return None
    address = item.get("address") if isinstance(item.get("address"), dict) else {}
    street = " ".join(
        part for part in (_plain(address.get("house_number") or ""), _plain(address.get("road") or "")) if part
    )
    city = _plain(address.get("city") or address.get("town") or address.get("village") or "")
    state = _plain(address.get("state") or "")
    postal = _plain(address.get("postcode") or "")
    tail = " ".join(part for part in (state, postal) if part)
    place = ", ".join(part for part in (city, tail) if part)
    line = ", ".join(part for part in (street, place) if part)
    if not line:
        line = _plain(item.get("display_name") or "")
    if not line:
        return None
    url = f"https://www.openstreetmap.org/?mlat={lat:.6f}&mlon={lon:.6f}#map=16/{lat:.6f}/{lon:.6f}"
    return Card(
        "OpenStreetMap",
        line,
        [("ADDRESS", line), ("LATLON", f"{lat:.6f}, {lon:.6f}")],
        url,
        _MAP_NOTE,
    )


def _gravatar(email: str) -> list[Card]:
    digest = hashlib.md5(email.strip().lower().encode("utf-8")).hexdigest()
    status, body, _error = fetch(f"https://en.gravatar.com/{digest}.json")
    if status != 200:
        return []
    card = _gravatar_card(_json(body))
    return [card] if card else []


def _gravatar_card(payload) -> Card | None:
    entry = payload.get("entry") if isinstance(payload, dict) else None
    if not isinstance(entry, list) or not entry or not isinstance(entry[0], dict):
        return None
    person = entry[0]
    name = person.get("name") if isinstance(person.get("name"), dict) else {}
    title = _plain(name.get("formatted") or person.get("displayName") or "")
    fields: list[tuple[str, str]] = []
    if title:
        fields.append(("NAME", title))
    location = _plain(person.get("currentLocation") or "")
    about = _plain(person.get("aboutMe") or "")
    phones = person.get("phoneNumbers")
    if isinstance(phones, list):
        for item in phones:
            if isinstance(item, dict) and _plain(item.get("value") or ""):
                fields.append(("PHONE", _plain(item.get("value") or "")))
                break
    if location:
        fields.append(("LOCATION", location))
    urls = person.get("urls")
    if isinstance(urls, list):
        for item in urls:
            if isinstance(item, dict) and str(item.get("value") or "").startswith("https://"):
                fields.append(("SITE", str(item["value"])))
                break
    if about:
        fields.append(("ABOUT", _clip(about, 180)))
    profile = str(person.get("profileUrl") or "")
    if profile.startswith("http://"):
        profile = "https://" + profile[len("http://") :]
    if not profile.startswith("https://") or not (title or fields):
        return None
    return Card("Gravatar", title or "Public profile", fields, profile)


def _title_ok(query: str, title: str) -> bool:
    words = [word for word in re.findall(r"[A-Za-z0-9']+", query.lower()) if len(word) > 1]
    if not words:
        return False
    haystack = title.lower()
    return all(re.search(rf"\b{re.escape(word)}\b", haystack) for word in words)


def _person_match(query: str, candidate: str) -> bool:
    first, _middle, last = split_name(query)
    if not last or not candidate:
        return False
    haystack = candidate.casefold()
    if not re.search(rf"\b{re.escape(last.casefold())}\b", haystack):
        return False
    if first and not re.search(rf"\b{re.escape(first.casefold())}\b", haystack):
        return False
    return True


def _json(body: bytes):
    try:
        return json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        return None


def _plain(value) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
