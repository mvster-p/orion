"""Public search links. Pages open in the browser. Nothing here is scraped."""

from __future__ import annotations

from urllib.parse import quote, quote_plus

from .models import (
    CORE_SOURCES,
    Query,
    Row,
    clean_handle,
    email_parts,
    handle_ok,
    phone_dashed,
    phone_digits,
    slug,
    split_name,
    valid_email,
)


def _g(text: str) -> str:
    return "https://www.google.com/search?q=" + quote_plus(text)


def _row(
    source: str,
    category: str,
    tier: str,
    url: str,
    detail: str,
    mode: str,
    core: bool = False,
) -> Row:
    return Row(
        source=source,
        category=category,
        tier=tier,
        status="ready",
        detail=detail,
        url=url,
        mode=mode,
        core=core,
    )


def _direct(
    source: str,
    url: str,
    tier: str,
    detail: str = "direct search page",
    category: str = "core",
    core: bool = False,
) -> Row:
    return _row(source, category, tier, url, detail, "direct", core)


def _pivot(source: str, domain: str, text: str, tier: str, core: bool = False, category: str = "core") -> Row:
    return _row(
        source,
        category,
        tier,
        _g(f'site:{domain} "{text}"'),
        "indexed pages",
        "pivot",
        core,
    )


def _core_direct(source: str, url: str, tier: str, detail: str = "direct search page") -> Row:
    return _direct(source, url, tier, detail, "core", True)


def _core_pivot(source: str, domain: str, text: str, tier: str) -> Row:
    return _pivot(source, domain, text, tier, True, "core")


def _city_slug(query: Query, lower: bool = False) -> str:
    bits = [query.city, query.state]
    text = " ".join(bit for bit in bits if bit)
    return slug(text, "-", lower) if text else ""


def _path(*parts: str) -> str:
    return "/".join(quote(part, safe="") for part in parts if part)


def _name_bits(query: Query) -> tuple[str, str, str]:
    return split_name(query.raw)


def _handle(query: Query) -> str | None:
    handle = clean_handle(query.raw)
    if handle_ok(handle):
        return handle
    return None


def _thats_them(query: Query) -> Row:
    source, tier = "ThatsThem", "free"
    if query.kind == "phone":
        digits = phone_digits(query.raw)
        if len(digits) >= 7:
            return _core_direct(source, f"https://thatsthem.com/phone/{phone_dashed(digits)}", tier)
        return _core_pivot(source, "thatsthem.com", query.needle, tier)
    if query.kind == "email" and valid_email(query.raw):
        return _core_direct(source, "https://thatsthem.com/email/" + quote(query.raw, safe="@"), tier)
    if query.kind == "address":
        return _core_direct(source, f"https://thatsthem.com/address/{slug(query.raw)}", tier)
    target = query.raw if query.kind == "name" else (_handle(query) or query.raw)
    loc = _city_slug(query)
    url = f"https://thatsthem.com/name/{slug(target)}"
    if loc:
        url += "/" + loc
    detail = "direct search page" if query.kind == "name" else "name search for this handle"
    return _core_direct(source, url, tier, detail)


def _whitepages(query: Query) -> Row:
    source, tier = "Whitepages", "preview"
    loc = _city_slug(query)
    if query.kind == "phone":
        digits = phone_digits(query.raw)
        if len(digits) == 10:
            dashed = phone_dashed(digits)
            return _core_direct(source, f"https://www.whitepages.com/phone/1-{dashed}", tier)
        if len(digits) >= 7:
            return _core_direct(source, f"https://www.whitepages.com/phone/{digits}", tier)
        return _core_pivot(source, "whitepages.com", query.needle, tier)
    if query.kind == "email" and valid_email(query.raw):
        return _core_direct(
            source,
            "https://www.whitepages.com/email/" + quote(query.raw, safe="@"),
            tier,
        )
    if query.kind == "address":
        url = f"https://www.whitepages.com/address/{slug(query.raw)}"
        if loc:
            url += "/" + loc
        return _core_direct(source, url, tier)
    target = query.raw if query.kind == "name" else (_handle(query) or query.raw)
    url = f"https://www.whitepages.com/name/{slug(target)}"
    if loc:
        url += "/" + loc
    detail = "direct search page" if query.kind == "name" else "name search for this handle"
    return _core_direct(source, url, tier, detail)


def _intelius(query: Query) -> Row:
    source, tier = "Intelius", "paid"
    if query.kind in {"name", "username"}:
        target = query.raw if query.kind == "name" else (_handle(query) or query.raw)
        detail = "direct search page" if query.kind == "name" else "name search for this handle"
        return _core_direct(source, f"https://www.intelius.com/people-search/{slug(target)}/", tier, detail)
    return _core_pivot(source, "intelius.com", query.needle, tier)


def _truepeople(query: Query) -> Row:
    source, tier = "TruePeopleSearch", "free"
    place = query.citystatezip
    if query.kind == "phone":
        digits = phone_digits(query.raw)
        if len(digits) >= 7:
            return _core_direct(
                source,
                "https://www.truepeoplesearch.com/resultphone?phoneno=" + quote(digits),
                tier,
            )
        return _core_pivot(source, "truepeoplesearch.com", query.needle, tier)
    if query.kind == "email" and valid_email(query.raw):
        return _core_direct(
            source,
            "https://www.truepeoplesearch.com/resultemail?email=" + quote_plus(query.raw),
            tier,
        )
    if query.kind == "address":
        url = "https://www.truepeoplesearch.com/resultaddress?streetaddress=" + quote_plus(query.raw)
        if place:
            url += "&citystatezip=" + quote_plus(place)
        return _core_direct(source, url, tier)
    target = query.raw if query.kind == "name" else (_handle(query) or query.raw)
    url = "https://www.truepeoplesearch.com/results?name=" + quote_plus(target)
    if place and query.kind == "name":
        url += "&citystatezip=" + quote_plus(place)
    detail = "direct search page" if query.kind == "name" else "name search for this handle"
    return _core_direct(source, url, tier, detail)


def _zaba(query: Query) -> Row:
    source, tier = "ZabaSearch", "free"
    if query.kind == "phone":
        digits = phone_digits(query.raw)
        if len(digits) >= 7:
            return _core_direct(source, f"https://www.zabasearch.com/phone/{digits}/", tier)
        return _core_pivot(source, "zabasearch.com", query.needle, tier)
    if query.kind == "email" and valid_email(query.raw):
        return _core_direct(
            source,
            "https://www.zabasearch.com/email/" + quote(query.raw, safe="@") + "/",
            tier,
        )
    if query.kind == "address":
        return _core_pivot(source, "zabasearch.com", query.needle, tier)
    target = query.raw if query.kind == "name" else (_handle(query) or query.raw)
    first, _middle, last = split_name(target)
    if first and last:
        path = slug(first, "+", True) + "+" + slug(last, "+", True)
    else:
        path = slug(target, "+", True)
    detail = "direct search page" if query.kind == "name" else "name search for this handle"
    return _core_direct(source, f"https://www.zabasearch.com/people/{path}/", tier, detail)


def _peekyou(query: Query) -> Row:
    source, tier = "PeekYou", "free"
    if query.kind == "email" and valid_email(query.raw):
        return _core_direct(
            source,
            "https://www.peekyou.com/email/search/?email=" + quote_plus(query.raw),
            tier,
        )
    if query.kind == "username" and _handle(query):
        return _core_direct(source, f"https://www.peekyou.com/{quote(_handle(query) or '')}", tier)
    if query.kind == "name":
        first, _middle, last = _name_bits(query)
        if first and last:
            path = slug(first, "_", True) + "_" + slug(last, "_", True)
        else:
            path = slug(query.raw, "_", True)
        return _core_direct(source, f"https://www.peekyou.com/{path}", tier)
    return _core_pivot(source, "peekyou.com", query.needle, tier)


def _truthfinder(query: Query) -> Row:
    source, tier = "TruthFinder", "paid"
    if query.kind == "name":
        first, _middle, last = _name_bits(query)
        url = (
            "https://www.truthfinder.com/results/?firstName="
            + quote_plus(first)
            + "&lastName="
            + quote_plus(last)
            + "&city="
            + quote_plus(query.city)
            + "&state="
            + quote_plus(query.state)
        )
        return _core_direct(source, url, tier)
    if query.kind == "phone":
        digits = phone_digits(query.raw)
        if len(digits) >= 7:
            return _core_direct(
                source,
                "https://www.truthfinder.com/results/?phone=" + quote(digits),
                tier,
            )
    return _core_pivot(source, "truthfinder.com", query.needle, tier)


def _us_search(query: Query) -> Row:
    source, tier = "US Search", "paid"
    if query.kind in {"name", "username"}:
        target = query.raw if query.kind == "name" else (_handle(query) or query.raw)
        path = slug(target, "-", True)
        url = f"https://www.ussearch.com/people-search/{path}/"
        if query.kind == "name" and query.city and query.state and len(query.state) <= 3:
            url += f"{quote(query.state.lower())}/{quote(slug(query.city, '-', True))}/"
        detail = "direct search page" if query.kind == "name" else "name search for this handle"
        return _core_direct(source, url, tier, detail)
    return _core_pivot(source, "ussearch.com", query.needle, tier)


def _spokeo(query: Query) -> Row:
    source, tier = "Spokeo", "preview"
    if query.kind == "phone":
        digits = phone_digits(query.raw)
        if len(digits) == 10:
            return _core_direct(source, f"https://www.spokeo.com/{phone_dashed(digits)}", tier)
        return _core_pivot(source, "spokeo.com", query.needle, tier)
    if query.kind == "email" and valid_email(query.raw):
        return _core_direct(source, "https://www.spokeo.com/email-search?q=" + quote_plus(query.raw), tier)
    if query.kind == "username" and _handle(query):
        return _core_direct(
            source,
            "https://www.spokeo.com/social/profile?q=" + quote_plus(_handle(query) or ""),
            tier,
        )
    if query.kind == "name":
        url = f"https://www.spokeo.com/{slug(query.raw)}"
        if query.state_name and query.city:
            url += "/" + _path(query.state_name, query.city)
        elif query.state_name:
            url += "/" + _path(query.state_name)
        return _core_direct(source, url, tier)
    return _core_pivot(source, "spokeo.com", query.needle, tier)


def _google(query: Query) -> Row:
    if query.kind == "phone":
        digits = phone_digits(query.raw)
        shown = phone_dashed(digits) if len(digits) in {7, 10} else query.raw
        text = f'"{shown}"'
    else:
        text = f'"{query.needle}"'
    return _core_direct("Google", _g(text), "free")


def _linkedin(query: Query) -> Row:
    if query.kind == "username" and _handle(query):
        url = "https://www.linkedin.com/in/" + quote(_handle(query) or "", safe="")
        return _core_direct("LinkedIn", url, "free", "profile page")
    keywords = query.needle
    url = "https://www.linkedin.com/search/results/people/?keywords=" + quote_plus(keywords)
    return _core_direct("LinkedIn", url, "free", "people search")


def _extra_people(query: Query) -> list[Row]:
    rows: list[Row] = []
    loc = _city_slug(query, lower=True)
    place = query.citystatezip
    if query.kind in {"name", "username"}:
        target = query.raw if query.kind == "name" else (_handle(query) or query.raw)
        low = slug(target, "-", True)
        title = slug(target)
        fast = f"https://www.fastpeoplesearch.com/name/{low}"
        if query.kind == "name" and loc:
            fast += "_" + loc
        rows.append(_direct("FastPeopleSearch", fast, "free", "direct search page", "people"))
        rows.append(
            _direct(
                "CyberBackground",
                f"https://www.cyberbackgroundchecks.com/people/{low}",
                "free",
                "direct search page",
                "people",
            )
        )
        find = f"https://www.searchpeoplefree.com/find/{low}"
        if query.kind == "name" and query.state and len(query.state) <= 3:
            find += "/" + quote(query.state.lower())
        rows.append(_direct("SearchPeopleFree", find, "free", "direct search page", "people"))
        rows.append(
            _direct(
                "411",
                f"https://www.411.com/name/{title}" + (f"/{_city_slug(query)}" if _city_slug(query) else ""),
                "preview",
                "direct search page",
                "people",
            )
        )
        first, _middle, last = split_name(target if query.kind == "name" else target)
        if query.kind == "username":
            first, last = "", target
        if first or last:
            url = "https://radaris.com/p/"
            url += quote(first or last)
            if first and last:
                url += "/" + quote(last)
            url += "/"
            rows.append(_direct("Radaris", url, "preview", "direct search page", "people"))
        family = (
            "https://www.familytreenow.com/search/genealogy/results?first="
            + quote_plus(first)
            + "&last="
            + quote_plus(last or target)
        )
        if place:
            family += "&citystatezip=" + quote_plus(place)
        rows.append(_direct("FamilyTreeNow", family, "free", "direct search page", "people"))
        rows.append(
            _direct(
                "BeenVerified",
                f"https://www.beenverified.com/people/{low}/",
                "paid",
                "direct search page",
                "people",
            )
        )
        rows.append(
            _direct(
                "Clustrmaps",
                f"https://clustrmaps.com/persons/{title}",
                "free",
                "direct search page",
                "people",
            )
        )
    if query.kind == "phone":
        digits = phone_digits(query.raw)
        if len(digits) >= 7:
            dashed = phone_dashed(digits)
            rows.append(
                _direct("FastPeopleSearch", f"https://www.fastpeoplesearch.com/{dashed}", "free", "direct search page", "people")
            )
            rows.append(
                _direct(
                    "CyberBackground",
                    f"https://www.cyberbackgroundchecks.com/phone/{dashed}",
                    "free",
                    "direct search page",
                    "people",
                )
            )
            rows.append(
                _direct(
                    "SearchPeopleFree",
                    f"https://www.searchpeoplefree.com/phone-lookup/{dashed}",
                    "free",
                    "direct search page",
                    "people",
                )
            )
            if len(digits) == 10:
                rows.append(
                    _direct("411", f"https://www.411.com/phone/1-{dashed}", "preview", "direct search page", "people")
                )
    if query.kind == "email" and valid_email(query.raw):
        rows.append(
            _direct(
                "CyberBackground",
                "https://www.cyberbackgroundchecks.com/email/" + quote(query.raw, safe="@"),
                "free",
                "direct search page",
                "people",
            )
        )
    if query.kind == "address":
        street = slug(query.raw, "-", True)
        tail = slug(" ".join(part for part in (query.city, query.state, query.postal) if part), "-", True)
        fast = f"https://www.fastpeoplesearch.com/address/{street}"
        if tail:
            fast += "_" + tail
        rows.append(_direct("FastPeopleSearch", fast, "free", "direct search page", "people"))
        cyber = f"https://www.cyberbackgroundchecks.com/address/{street}"
        if query.city:
            cyber += "_" + slug(query.city, "-", True)
        if query.state:
            cyber += "-" + slug(query.state, "-", True)
        rows.append(_direct("CyberBackground", cyber, "free", "direct search page", "people"))
        rows.append(
            _direct(
                "Clustrmaps",
                "https://clustrmaps.com/search?q=" + quote_plus(query.needle),
                "free",
                "direct search page",
                "people",
            )
        )
    return rows


def _social(query: Query) -> list[Row]:
    rows: list[Row] = []
    handle = _handle(query) if query.kind == "username" else None
    if handle:
        pages = [
            ("Facebook", f"https://www.facebook.com/{quote(handle)}"),
            ("X", f"https://x.com/{quote(handle)}"),
            ("Instagram", f"https://www.instagram.com/{quote(handle)}/"),
            ("TikTok", f"https://www.tiktok.com/@{quote(handle)}"),
            ("YouTube", f"https://www.youtube.com/@{quote(handle)}"),
            ("Reddit", f"https://www.reddit.com/user/{quote(handle)}"),
            ("GitHub", f"https://github.com/{quote(handle)}"),
            ("GitLab", f"https://gitlab.com/{quote(handle)}"),
            ("Telegram", f"https://t.me/{quote(handle)}"),
            ("Pinterest", f"https://www.pinterest.com/{quote(handle)}/"),
            ("Keybase", f"https://keybase.io/{quote(handle)}"),
            ("Dev.to", f"https://dev.to/{quote(handle)}"),
            ("Twitch", f"https://www.twitch.tv/{quote(handle)}"),
            ("Steam", f"https://steamcommunity.com/id/{quote(handle)}"),
            ("Linktree", f"https://linktr.ee/{quote(handle)}"),
            ("Mastodon", f"https://mastodon.social/@{quote(handle)}"),
            ("Bluesky", f"https://bsky.app/profile/{quote(handle.lower())}.bsky.social"),
            ("Wikipedia", f"https://en.wikipedia.org/wiki/User:{quote(handle)}"),
        ]
        for source, url in pages:
            rows.append(_direct(source, url, "free", "profile page", "social"))
        return rows
    if query.kind == "name":
        text = query.needle
        rows.append(
            _direct(
                "Facebook",
                "https://www.facebook.com/search/people/?q=" + quote_plus(text),
                "free",
                "people search",
                "social",
            )
        )
        rows.append(
            _direct(
                "X",
                "https://x.com/search?f=user&q=" + quote_plus(text),
                "free",
                "people search",
                "social",
            )
        )
        rows.append(
            _direct(
                "TikTok",
                "https://www.tiktok.com/search/user?q=" + quote_plus(text),
                "free",
                "people search",
                "social",
            )
        )
        rows.append(
            _direct(
                "YouTube",
                "https://www.youtube.com/results?search_query=" + quote_plus(text),
                "free",
                "video search",
                "social",
            )
        )
        rows.append(
            _direct(
                "Reddit",
                "https://www.reddit.com/search/?q=" + quote_plus(text),
                "free",
                "post search",
                "social",
            )
        )
        rows.append(
            _direct(
                "GitHub",
                "https://github.com/search?type=users&q=" + quote_plus(text),
                "free",
                "user search",
                "social",
            )
        )
    if query.kind == "email" and valid_email(query.raw):
        rows.append(
            _direct(
                "GitHub",
                "https://github.com/search?type=users&q=" + quote_plus(query.raw),
                "free",
                "user search",
                "social",
            )
        )
    return rows


def _web(query: Query) -> list[Row]:
    text = f'"{query.needle}"'
    rows = [
        _direct("Bing", "https://www.bing.com/search?q=" + quote_plus(text), "free", "web search", "web"),
        _direct("DuckDuckGo", "https://duckduckgo.com/?q=" + quote_plus(text), "free", "web search", "web"),
        _direct("Yandex", "https://yandex.com/search/?text=" + quote_plus(text), "free", "web search", "web"),
    ]
    if query.kind == "name":
        rows.append(_direct("Google", _g(f'site:linkedin.com/in "{query.raw}"'), "free", "indexed LinkedIn profiles", "web"))
        rows.append(_direct("Google", _g(f'site:facebook.com "{query.raw}"'), "free", "indexed Facebook pages", "web"))
        rows.append(_direct("Google", _g(f'filetype:pdf "{query.raw}"'), "free", "indexed PDF documents", "web"))
        rows.append(
            _direct(
                "Wikipedia",
                "https://en.wikipedia.org/w/index.php?search=" + quote_plus(query.raw),
                "free",
                "article search",
                "web",
            )
        )
    elif query.kind == "username":
        handle = _handle(query) or query.raw
        rows.append(_direct("Google", _g(f'site:pastebin.com "{handle}"'), "free", "indexed public pastes", "web"))
        rows.append(_direct("Google", _g(f'site:x.com "{handle}"'), "free", "indexed X pages", "web"))
    elif query.kind == "email":
        rows.append(_direct("Google", _g(f'site:linkedin.com "{query.raw}"'), "free", "indexed LinkedIn pages", "web"))
        rows.append(_direct("Google", _g(f'site:github.com "{query.raw}"'), "free", "indexed GitHub pages", "web"))
    elif query.kind == "phone":
        digits = phone_digits(query.raw)
        if len(digits) >= 7 and phone_dashed(digits) != digits:
            rows.append(_direct("Google", _g(f'"{digits}"'), "free", "digits-only search", "web"))
    elif query.kind == "address":
        rows.append(_direct("Google", _g(f'site:redfin.com "{query.needle}"'), "free", "indexed Redfin pages", "web"))
    return rows


def _records(query: Query) -> list[Row]:
    rows: list[Row] = []
    if query.kind in {"name", "username"}:
        text = query.raw
        rows.append(
            _direct(
                "OpenCorporates",
                "https://opencorporates.com/companies?q=" + quote_plus(text),
                "free",
                "company search",
                "records",
            )
        )
        rows.append(
            _direct(
                "CourtListener",
                "https://www.courtlistener.com/?type=p&q=" + quote_plus(text),
                "free",
                "party search",
                "records",
            )
        )
        rows.append(
            _direct(
                "SEC EDGAR",
                "https://www.sec.gov/edgar/search/#/q=" + quote_plus(text),
                "free",
                "filing search",
                "records",
            )
        )
    if query.kind == "email" and valid_email(query.raw):
        _local, domain = email_parts(query.raw)
        rows.append(
            _direct(
                "HIBP",
                "https://haveibeenpwned.com/account/" + quote(query.raw),
                "free",
                "Have I Been Pwned",
                "records",
            )
        )
        rows.append(
            _direct("crt.sh", "https://crt.sh/?q=" + quote(domain), "free", "certificate search", "records")
        )
        rows.append(
            _direct(
                "Whois",
                "https://www.whois.com/whois/" + quote(domain),
                "free",
                "domain registration",
                "records",
            )
        )
        rows.append(
            _direct(
                "Hunter",
                "https://hunter.io/search/" + quote(domain),
                "preview",
                "domain search page",
                "records",
            )
        )
    return rows


def _maps(query: Query) -> list[Row]:
    if query.kind != "address":
        return []
    full = query.needle
    return [
        _direct(
            "Google Maps",
            "https://www.google.com/maps/search/?api=1&query=" + quote_plus(full),
            "free",
            "map search",
            "maps",
        ),
        _direct(
            "OpenStreetMap",
            "https://www.openstreetmap.org/search?query=" + quote_plus(full),
            "free",
            "map search",
            "maps",
        ),
        _direct(
            "Zillow",
            "https://www.zillow.com/homes/" + quote_plus(full) + "_rb/",
            "free",
            "property search",
            "maps",
        ),
    ]


def build_links(query: Query) -> list[Row]:
    if query.kind == "image":
        from .image_meta import image_rows

        return image_rows(query)
    if query.kind == "websight":
        from .websight import websight_rows

        return websight_rows(query)
    rows = [
        _thats_them(query),
        _whitepages(query),
        _intelius(query),
        _truepeople(query),
        _zaba(query),
        _peekyou(query),
        _truthfinder(query),
        _us_search(query),
        _spokeo(query),
        _google(query),
        _linkedin(query),
    ]
    rows.extend(_extra_people(query))
    rows.extend(_social(query))
    rows.extend(_web(query))
    rows.extend(_records(query))
    rows.extend(_maps(query))
    order = {name: index for index, name in enumerate(CORE_SOURCES)}
    cat_order = {name: index for index, name in enumerate(("meta", "core", "people", "social", "web", "records", "maps"))}

    def sort_key(row: Row) -> tuple:
        return (cat_order.get(row.category, 99), order.get(row.source, 99), row.source, row.detail)

    rows.sort(key=sort_key)
    for row in rows:
        if " " in row.url or not row.url.startswith("https://"):
            raise ValueError(f"bad url for {row.source}: {row.url}")
    return rows
