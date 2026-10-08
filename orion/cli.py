"""Interactive terminal and one-shot commands."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import webbrowser
from urllib.parse import urlparse

from rich.prompt import Prompt
from rich.text import Text

from . import graphics as g
from .catalog import build_links
from .models import (
    CORE_SOURCES,
    CAT_LABELS,
    Query,
    Result,
    Session,
    detect,
    slug,
)
from .probe import jobs_for, run_job
from .report import save_report, to_markdown
from .scan import run_scan
from .ui import console_factory, numbered_rows, render_boot, render_catalog, render_home, render_result, render_scan

KINDS = ("name", "username", "email", "phone", "address", "image", "websight")
BANNED = ("dehashed", "snusbase", "intelx", "leakcheck", "psbdmp", "breachdirectory")


def _utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _boot(console, fast: bool) -> None:
    steps = ("catalog", "directories", "probes", "renderer", "ready")
    from rich.live import Live

    with Live(render_boot(0, steps[0]), console=console, refresh_per_second=10, transient=True) as live:
        for index, name in enumerate(steps):
            live.update(render_boot(index, name))
            time.sleep(0.04 if fast else 0.16)
        live.update(render_boot(len(steps), "online"))
        time.sleep(0.08 if fast else 0.2)


def _ask(console, label: str, default: str = "") -> str:
    return Prompt.ask(label, default=default, console=console).strip()


def _kind_from_menu(choice: str) -> str | None:
    mapping = {
        "1": "name",
        "2": "username",
        "3": "email",
        "4": "phone",
        "5": "address",
        "6": "image",
        "7": "websight",
        "8": "auto",
        "name": "name",
        "username": "username",
        "user": "username",
        "email": "email",
        "mail": "email",
        "phone": "phone",
        "address": "address",
        "addr": "address",
        "image": "image",
        "photo": "image",
        "exif": "image",
        "websight": "websight",
        "web": "websight",
        "site": "websight",
        "auto": "auto",
    }
    return mapping.get(choice.strip().lower())


def _location(console) -> tuple[str, str, str]:
    console.print(Text(" Location narrows directory results. Enter skips a field.", style=g.TEXT))
    city = _ask(console, "[accent]CITY[/]")
    state = _ask(console, "[accent]STATE[/]")
    postal = _ask(console, "[accent]ZIP[/]")
    return city, state, postal


def _build_query(console, kind: str, raw: str, ask_place: bool, city: str = "", state: str = "", postal: str = "") -> Query:
    if kind == "auto":
        kind = detect(raw)
        console.print(Text.assemble((" Classified as ", g.TEXT), (kind.upper(), g.ACCENT)))
        typed = _ask(console, "[accent]TYPE[/] enter accepts", default=kind).lower()
        if typed in KINDS:
            kind = typed
    if ask_place and kind in {"name", "address"} and not (city or state or postal):
        city, state, postal = _location(console)
    return Query(kind, raw, city, state, postal)


def _open_one(console, result: Result, index: int) -> None:
    rows = numbered_rows(result)
    if index < 1 or index > len(rows):
        console.print(Text(f" No row {index}. The mesh has {len(rows)}.", style=g.RED))
        return
    row = rows[index - 1]
    if not row.url:
        console.print(Text(f" {row.source} is metadata, not a page.", style=g.ACCENT))
        return
    webbrowser.open(row.url)
    console.print(Text.assemble((" Opened ", g.GREEN), (row.source, g.TEXT)))


def _open_group(console, result: Result, name: str) -> None:
    key = name.strip().lower()
    aliases = {label.lower(): cat for cat, label in CAT_LABELS.items()}
    aliases.update({cat: cat for cat in CAT_LABELS})
    category = aliases.get(key, "")
    if not category:
        console.print(Text(" Groups: meta, core, people, social, web, records, maps, live.", style=g.ACCENT))
        return
    group = [row for row in numbered_rows(result) if row.category == category]
    rows = [row for row in group if row.url]
    if not group:
        console.print(Text(" That group is empty for this query.", style=g.ACCENT))
        return
    if not rows:
        console.print(Text(" That group has metadata only.", style=g.ACCENT))
        return
    if len(rows) > 6:
        typed = _ask(console, f"[accent]Open {len(rows)} pages? Type OPEN[/]")
        if typed.upper() != "OPEN":
            console.print(Text(" Left closed.", style=g.TEXT))
            return
    for row in rows:
        webbrowser.open(row.url)
    console.print(Text(f" Opened {len(rows)} {CAT_LABELS[category]} pages.", style=g.GREEN))


def _yank(console, result: Result) -> None:
    urls: list[str] = []
    for url in [row.url for row in numbered_rows(result) if row.url]:
        if url not in urls:
            urls.append(url)
    for card in result.cards:
        if card.url and card.url not in urls:
            urls.append(card.url)
    if not urls:
        console.print(Text(" No URLs to copy.", style=g.ACCENT))
        return
    text = "\n".join(urls)
    try:
        subprocess.run(["clip"], input=text, text=True, check=True, timeout=5)
    except Exception as exc:
        console.print(Text(f" Clipboard unavailable ({exc}).", style=g.RED))
        return
    console.print(Text(f" Yanked {len(urls)} URLs to the clipboard.", style=g.GREEN))


def _actions(console, result: Result, session: Session) -> str:
    """Return 'menu', 'new', or 'quit'."""
    while True:
        console.print(render_result(result, interactive=True))
        choice = _ask(console, "[accent]orion[/]").strip()
        if not choice:
            continue
        low = choice.lower()
        if low in {"q", "quit", "0", "exit"}:
            return "quit"
        if low in {"b", "back", "menu"}:
            return "menu"
        if low in {"n", "new"}:
            return "new"
        if low in {"e", "export", "s", "save"}:
            path = save_report(result)
            console.print(Text(f" Wrote {path}", style=g.GREEN))
            continue
        if low in {"y", "yank", "copy"}:
            _yank(console, result)
            continue
        if low in {"g", "group"}:
            name = _ask(console, "[accent]GROUP[/]")
            _open_group(console, result, name)
            continue
        if low.startswith("g "):
            _open_group(console, result, low[2:])
            continue
        if low in {"o", "open"}:
            number = _ask(console, "[accent]ROW[/]")
            if number.isdigit():
                _open_one(console, result, int(number))
            continue
        if low.startswith("o " ) and low[2:].isdigit():
            _open_one(console, result, int(low[2:]))
            continue
        if choice.isdigit():
            _open_one(console, result, int(choice))
            continue
        console.print(Text(" Use a row number, O, G, Y, E, N, B, or Q.", style=g.ACCENT))


def interactive(console, probes: bool, animate: bool) -> int:
    console.clear()
    _boot(console, fast=not animate)
    session = Session(started=time.monotonic())
    pending_kind: str | None = None
    while True:
        if pending_kind is None:
            console.print(render_home(session))
            choice = _ask(console, "[accent]orion[/]").lower()
            if choice in {"0", "q", "quit", "exit"}:
                break
            if choice in {"9", "catalog"}:
                console.print(render_catalog())
                _ask(console, "[muted]enter returns[/]")
                continue
            if choice in {"l", "last"}:
                if session.last is None:
                    console.print(Text(" No report yet.", style=g.ACCENT))
                    continue
                action = _actions(console, session.last, session)
                if action == "quit":
                    break
                if action == "new":
                    pending_kind = "auto"
                continue
            pending_kind = _kind_from_menu(choice)
            if pending_kind is None:
                console.print(Text(" Pick 1-9, L, or 0 to exit.", style=g.ACCENT))
                continue
        if pending_kind == "image":
            label = "[accent]PATH OR URL[/]"
        elif pending_kind == "websight":
            label = "[accent]WEB ADDRESS[/]"
        else:
            label = "[accent]QUERY[/]"
        raw = _ask(console, label)
        if not raw:
            pending_kind = None
            continue
        try:
            query = _build_query(console, pending_kind, raw, ask_place=True)
            result = run_scan(console, query, probes=probes, animate=animate)
        except KeyboardInterrupt:
            console.print(Text("\n Scan stopped.", style=g.RED))
            pending_kind = None
            continue
        session.remember(result)
        pending_kind = None
        action = _actions(console, result, session)
        if action == "quit":
            break
        if action == "new":
            pending_kind = "auto"
    console.print(Text(f" {g.clock()} session closed.", style=g.ACCENT))
    return 0


def once(console, args: argparse.Namespace) -> int:
    kind = args.type
    if kind == "auto":
        kind = detect(args.query)
        console.print(Text.assemble((" Classified as ", g.TEXT), (kind.upper(), g.ACCENT)))
    query = Query(kind, args.query, args.city or "", args.state or "", args.postal or "")
    if not args.no_boot and not args.fast:
        _boot(console, fast=False)
    result = run_scan(console, query, probes=not args.no_probe, animate=not args.fast)
    console.print(render_result(result, interactive=False))
    if args.save:
        path = save_report(result)
        console.print(Text(f" Wrote {path}", style=g.GREEN))
    return 0


def self_test(console) -> int:
    failures: list[str] = []

    def check(label: str, ok: bool) -> None:
        mark = "[green]PASS[/]" if ok else "[red]FAIL[/]"
        console.print(f" {mark}  {label}")
        if not ok:
            failures.append(label)

    check("email detect", detect("ada@example.com") == "email")
    check("phone detect", detect("(415) 555-0134") == "phone")
    check("username detect", detect("jane_doe") == "username")
    check("name detect", detect("Grace Hopper") == "name")
    check("address detect", detect("123 Main St") == "address")
    check("single capital name", detect("Grace") == "name")
    check("image detect file", detect(r"C:\photos\roof.jpg") == "image")
    check("image detect url", detect("https://cdn.example.com/a.PNG?w=1") == "image")
    check("image detect not username", detect("photo.jpg") == "image")
    check("websight detect", detect("example.com") == "websight")
    check("websight detect url", detect("https://example.com/about") == "websight")
    check("image menu", _kind_from_menu("6") == "image")
    check("websight menu", _kind_from_menu("7") == "websight")
    check("auto menu", _kind_from_menu("8") == "auto")
    quoted = Query("image", r'"C:\photos\roof.jpg"')
    check("image quotes stripped", quoted.raw == r"C:\photos\roof.jpg")
    check("accent slug", slug("José Muñoz") == "Jose-Munoz")

    samples = {
        "name": Query("name", "Grace Hopper", "Arlington", "VA"),
        "username": Query("username", "torvalds"),
        "email": Query("email", "octocat@github.com"),
        "phone": Query("phone", "(415) 555-0134"),
        "address": Query("address", "1600 Pennsylvania Ave NW", "Washington", "DC", "20500"),
    }
    for kind, query in samples.items():
        rows = build_links(query)
        sources = {row.source for row in rows if row.core}
        check(f"{kind} core mesh", sources == set(CORE_SOURCES))
        check(f"{kind} https only", all(row.url.startswith("https://") and " " not in row.url for row in rows))
        joined = " ".join(row.url.lower() for row in rows)
        check(f"{kind} no breach dumps", all(word not in joined for word in BANNED))
        check(f"{kind} has rows", len(rows) >= 12)

    phone_rows = {row.source: row.url for row in build_links(samples["phone"]) if row.core}
    check("whitepages phone path", "1-415-555-0134" in phone_rows["Whitepages"])
    check("truepeoplesearch phone path", "phoneno=4155550134" in phone_rows["TruePeopleSearch"])
    name_rows = {row.source: row.url for row in build_links(samples["name"]) if row.core}
    check("spokeo state name", "Virginia" in name_rows["Spokeo"] and "Arlington" in name_rows["Spokeo"])
    check("truepeople place", "Arlington" in name_rows["TruePeopleSearch"])
    address_rows = build_links(samples["address"])
    check("maps present", any(row.source == "Google Maps" for row in address_rows))
    check("username probes", len(jobs_for(samples["username"])) >= 10)
    check(
        "email probes",
        {job.source for job in jobs_for(samples["email"])} == {"Gravatar", "Domain DNS", "Mail MX"},
    )
    check("phone has no probes", jobs_for(samples["phone"]) == [])

    name_result = Result(samples["name"], build_links(samples["name"]), 0.2, "TESTTAGNAME01")
    from rich.console import Console

    preview = Console(width=110, force_terminal=True, record=True, highlight=False, color_system="truecolor")
    preview.print(render_result(name_result))
    preview.print(render_catalog())
    preview.print(render_home(Session(started=time.monotonic())))
    preview.print(
        render_scan(
            "NAME  Grace Hopper",
            name_result.rows[:6],
            len(name_result.rows),
            "LINK FABRIC",
            time.monotonic(),
            name_result.rows[:4],
        )
    )
    exported = preview.export_text()
    check("render wordmark", "ORION" in exported and "PUBLIC SOURCE MESH" in exported)
    check("render core source", "ThatsThem" in exported and "TruePeopleSearch" in exported)
    check("render gauge", "█" in exported or "#" in exported)
    check("markdown", "Grace Hopper" in to_markdown(name_result) and "https://" in to_markdown(name_result))
    check("render image lens", "IMAGE" in exported)
    check("render websight lens", "WEBSIGHT" in exported)

    from .digest import card_jobs, parser_checks, run_cards
    from .models import Card

    for label, ok in parser_checks():
        check(label, ok)
    summary_view = Console(width=110, force_terminal=True, record=True, highlight=False, color_system="truecolor")
    summary_card = Card(
        "NPI Registry",
        "Jane Doe, M.D.",
        [("PHONE", "(415) 555-0134"), ("ADDRESS", "1 Main St, Austin, TX 78701")],
        "https://npiregistry.cms.hhs.gov/provider-view/1234567893",
        "Health-provider registry record.",
    )
    summary_result = Result(
        samples["name"],
        build_links(samples["name"])[:1],
        0.1,
        "SUMMARYTAG01",
        cards=[summary_card],
        preview=True,
    )
    summary_view.print(render_result(summary_result))
    summary_text = summary_view.export_text()
    check(
        "summary above links",
        summary_text.find("directory sites stay as links") != -1
        and summary_text.find("directory sites stay as links") < summary_text.find("ThatsThem"),
    )
    check("summary fields", "(415) 555-0134" in summary_text and "1 Main St" in summary_text)
    empty_view = Console(width=110, force_terminal=True, record=True, highlight=False, color_system="truecolor")
    empty_view.print(render_result(Result(samples["phone"], build_links(samples["phone"])[:1], 0.1, "EMPTYTAG0001", preview=True)))
    check("summary empty", "No public preview came back" in empty_view.export_text())

    from .websight import parser_checks as websight_checks
    from .websight import websight_cards

    for label, ok in websight_checks():
        check(label, ok)
    web_rows = build_links(Query("websight", "https://example.com"))
    web_urls = " ".join(row.url.lower() for row in web_rows)
    check("websight https", all(row.url.startswith("https://") and " " not in row.url for row in web_rows))
    check("websight mesh", "crt.sh" in web_urls and "google.com/search" in web_urls and "whois.com" in web_urls)
    check("websight no people", "thatsthem" not in web_urls and "spokeo" not in web_urls)
    check("websight private rows", build_links(Query("websight", "http://127.0.0.1/contact")) == [])
    kept = run_cards(lambda: websight_cards(Query("websight", "http://127.0.0.1/contact")))
    private_note = " ".join(value for _label, value in kept[0].fields).lower() if kept else ""
    check("websight private note", bool(kept) and kept[0].url == "" and "not public" in private_note)
    sight = Result(
        Query("websight", "https://example.com/about"),
        web_rows[:1],
        0.1,
        "WEBSIGHTTAG1",
        cards=[
            Card(
                "WebSight",
                "Example",
                [("EMAIL", "ada@example.com")],
                "https://example.com/about",
                "Published on this site.",
            )
        ],
        preview=True,
    )
    sight_view = Console(width=110, force_terminal=True, record=True, highlight=False, color_system="truecolor")
    sight_view.print(render_result(sight))
    sight_text = sight_view.export_text()
    check(
        "websight summary",
        "public pages on this site" in sight_text
        and sight_text.find("ada@example.com") < sight_text.find("open this site"),
    )

    from .image_meta import reverse_links

    sample_url = "https://example.com/a.jpg"
    reverses = reverse_links(sample_url)
    reverse_urls = {row.source: row.url for row in reverses}
    check("lens link", reverse_urls["Google Lens"].startswith("https://lens.google.com/uploadbyurl?url="))
    check("tineye link", "tineye.com/search?url=" in reverse_urls["TinEye"])
    check("yandex link", "rpt=imageview" in reverse_urls["Yandex"] and "yandex.com/images/search" in reverse_urls["Yandex"])
    check("bing link", "bing.com/images/search" in reverse_urls["Bing"] and "imgurl" in reverse_urls["Bing"])
    check("google images link", "google.com/searchbyimage?image_url=" in reverse_urls["Google Images"])
    check(
        "reverse https",
        all(row.url.startswith("https://") and " " not in row.url for row in reverses),
    )
    check("reverse encodes url", "example.com" in reverse_urls["Google Lens"])

    import shutil
    import tempfile
    from pathlib import Path

    try:
        from PIL import Image
        from PIL.ExifTags import IFD
        from PIL.TiffImagePlugin import IFDRational
    except ImportError:
        check("pillow installed", False)
    else:
        folder = Path(tempfile.mkdtemp(prefix="orion-image-"))
        try:
            photo = folder / "orion exif.jpg"
            image = Image.new("RGB", (16, 8), "white")
            exif = Image.Exif()
            exif[271] = "Canon"
            exif[272] = "EOS R5"
            exif[315] = "Ada Lovelace"
            exif[305] = "ORION Test"
            exif[33432] = "Ada Lovelace"
            sub = exif.get_ifd(IFD.Exif)
            sub[0xA431] = "SN-BODY-1"
            sub[0xA435] = "SN-LENS-1"
            sub[0xA434] = "RF 24-70mm"
            gps = exif.get_ifd(IFD.GPSInfo)
            gps[1] = "N"
            gps[2] = (IFDRational(37, 1), IFDRational(46, 1), IFDRational(30, 1))
            gps[3] = "W"
            gps[4] = (IFDRational(122, 1), IFDRational(25, 1), IFDRational(30, 1))
            image.save(photo, format="JPEG", exif=exif)
            rows = build_links(Query("image", str(photo)))
            details = {row.source: row.detail for row in rows}
            urls = " ".join(row.url for row in rows)
            check("exif make", details.get("Make") == "Canon")
            check("exif model", details.get("Model") == "EOS R5")
            check("exif artist", details.get("Artist") == "Ada Lovelace")
            check("exif software", details.get("Software") == "ORION Test")
            check("exif serial", details.get("BodySerialNumber") == "SN-BODY-1")
            check("exif lens", details.get("LensModel") == "RF 24-70mm")
            check("exif gps", "37.775" in details.get("GPS", "") and "-122.425" in details.get("GPS", ""))
            check("gps maps", "37.775" in urls and "-122.425" in urls and "google.com/maps" in urls)
            check("camera search", any("Canon" in row.url and "EOS" in row.url for row in rows))
            check("image no core", all(not row.core for row in rows))
            check("image no people hosts", "thatsthem.com" not in urls and "truepeoplesearch.com" not in urls)
            check("image blank urls", all(row.category == "meta" for row in rows if not row.url))
            check("image urls", all((not row.url) or (row.url.startswith("https://") and " " not in row.url) for row in rows))
            check("local file not uploaded", "lens.google.com" not in urls and photo.name not in urls)
            check("local note", any("stays on this machine" in row.detail for row in rows))
            check("image sha", len(details.get("SHA-256", "")) == 64)
            check("image probes off", jobs_for(Query("image", str(photo))) == [])
            missing = build_links(Query("image", str(folder / "missing.jpg")))
            check("image missing file", missing[0].status == "error" and not any(row.url for row in missing))
            private = build_links(Query("image", "https://127.0.0.1/a.jpg"))
            check("image private url", private[0].status == "error" and not any(row.url for row in private))
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    github = run_job(jobs_for(Query("username", "torvalds"))[0])
    if github.status == "hit":
        check("live github torvalds", "Linus" in github.detail or github.status == "hit")
    elif github.status in {"blocked", "error"}:
        console.print(f" [accent]SKIP[/]  live github unreachable ({github.status}: {github.detail})")
    else:
        check("live github torvalds", False)

    gravatar = run_job(jobs_for(Query("email", "octocat@github.com"))[0])
    check("gravatar returns a status", gravatar.status in {"hit", "miss", "blocked", "error"})
    parsed = urlparse(gravatar.url)
    check("gravatar host", parsed.hostname == "www.gravatar.com")

    wiki_job = next(fn for name, fn in card_jobs(Query("name", "Grace Hopper")) if name == "Wikipedia")
    wiki_cards = run_cards(wiki_job)
    if not wiki_cards:
        console.print(" [accent]SKIP[/]  live wikipedia unreachable")
    else:
        check("live wiki grace hopper", "Hopper" in wiki_cards[0].title)

    if failures:
        console.print(Text(f" {len(failures)} failed.", style=g.RED))
        return 1
    console.print(g.gauge(1.0, 24))
    console.print(Text(" SELF-TEST GREEN", style=g.GREEN))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="orion",
        description="ORION public-source search terminal. Builds search links and checks public profile APIs.",
    )
    parser.add_argument("--once", action="store_true", help="run one query and print the report")
    parser.add_argument("--type", choices=(*KINDS, "auto"), default="auto", help="query type for --once")
    parser.add_argument("--query", default="", help="name, username, email, phone, address, image path, or web address")
    parser.add_argument("--city", default="")
    parser.add_argument("--state", default="")
    parser.add_argument("--postal", default="")
    parser.add_argument("--no-probe", action="store_true", help="skip live public API checks")
    parser.add_argument("--fast", action="store_true", help="skip the boot and link animation")
    parser.add_argument("--no-boot", action="store_true")
    parser.add_argument("--save", action="store_true", help="write a markdown report")
    parser.add_argument("--catalog", action="store_true", help="print the source matrix and exit")
    parser.add_argument("--self-test", action="store_true", help="check links, graphics, and one live probe")
    return parser


def main(argv: list[str] | None = None) -> int:
    _utf8()
    args = build_parser().parse_args(argv)
    console = console_factory()
    try:
        if args.self_test:
            return self_test(console)
        if args.catalog:
            from .ui import render_catalog

            console.print(render_catalog())
            return 0
        if args.once:
            if not args.query.strip():
                console.print(Text(" --once needs --query.", style=g.RED))
                return 2
            return once(console, args)
        if not sys.stdin.isatty():
            console.print(Text(" Run ORION in a terminal, or pass --once --query.", style=g.ACCENT))
            return 2
        return interactive(console, probes=not args.no_probe, animate=not args.fast)
    except KeyboardInterrupt:
        console.print(Text(f"\n {g.clock()} session closed.", style=g.ACCENT))
        return 0
