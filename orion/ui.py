"""Color panels, meters, and the scan readout."""

from __future__ import annotations

import time
from collections import Counter

from rich import box
from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import graphics as g
from .catalog import build_links
from .models import CAT_LABELS, CAT_ORDER, CORE_SOURCES, Result, Row, Session
from .probe import jobs_for


def console_factory() -> Console:
    return Console(highlight=False, theme=g.theme())


def _panel(renderable: RenderableType, title: str, subtitle: str = "") -> Panel:
    return Panel(
        renderable,
        title=Text(title, style=g.ACCENT),
        subtitle=Text(subtitle, style=g.TEXT) if subtitle else None,
        border_style=g.NAVY,
        box=box.ROUNDED,
        padding=(0, 1),
    )


def _session_line(session: Session) -> Text:
    elapsed = max(0, int(time.monotonic() - session.started))
    minutes, seconds = divmod(elapsed, 60)
    line = Text()
    line.append(f" {g.clock(time.monotonic())} ", style=g.ACCENT)
    line.append(f"{minutes:02d}:{seconds:02d}", style=g.TEXT)
    line.append("   SEARCHES ", style=g.TEXT)
    line.append(str(session.searches), style=g.ACCENT)
    line.append("   LINKS ", style=g.TEXT)
    line.append(str(session.links), style=g.ACCENT)
    line.append("   HITS ", style=g.TEXT)
    line.append(str(session.hits), style=g.ACCENT)
    line.append("   SIGNAL ", style=g.TEXT)
    line.append_text(g.sparkline([float(item) for item in session.hit_track] or [0.0]))
    return line


def mesh_size() -> tuple[int, int]:
    from .models import Query

    samples = {
        "name": Query("name", "Jane Doe", "Austin", "TX"),
        "username": Query("username", "janedoe"),
        "email": Query("email", "jane@example.com"),
        "phone": Query("phone", "4155550134"),
        "address": Query("address", "1 Main St", "Austin", "TX", "78701"),
    }
    sources: set[str] = set()
    for query in samples.values():
        for row in build_links(query):
            sources.add(row.source)
        for job in jobs_for(query):
            sources.add(job.source)
    probes = len(jobs_for(samples["username"])) + len(jobs_for(samples["email"]))
    return len(sources), probes


def render_boot(done: int, active: str) -> RenderableType:
    steps = [
        ("CATALOG", "11 core sources locked"),
        ("DIRECTORIES", "public people-search pages"),
        ("PROBE MESH", "public APIs only"),
        ("RENDERER", "terminal graphics online"),
        ("READY", "no private-record scrape"),
    ]
    table = Table.grid(padding=(0, 1))
    table.add_column(width=3)
    table.add_column(width=14)
    table.add_column(width=22)
    table.add_column()
    for index, (name, detail) in enumerate(steps):
        if index < done:
            mark = Text(" ✓ " if g.FANCY else " + ", style=g.GREEN)
            label = Text(name, style=g.GREEN)
            bar = g.gauge(1.0, 12)
        elif index == done:
            mark = Text(f" {g.clock()} ", style=g.ACCENT)
            label = Text(name, style=g.ACCENT)
            bar = g.gauge(0.45 + (time.monotonic() % 0.4), 12)
        else:
            mark = Text(" · ", style=g.NAVY)
            label = Text(name, style=g.NAVY)
            bar = g.gauge(0.0, 12)
        table.add_row(mark, label, bar, Text(detail, style=g.TEXT))
    title = f" {g.clock()}  BRING-UP "
    return Group(g.logo(), Text(""), _panel(table, title, active))


def render_home(session: Session) -> RenderableType:
    sources, probes = mesh_size()
    menu = Table.grid(padding=(0, 2), expand=True)
    menu.add_column(width=4)
    menu.add_column(width=12)
    menu.add_column()
    items = [
        ("1", "NAME", "directories, web, public records"),
        ("2", "USERNAME", "profile pages + live public checks"),
        ("3", "EMAIL", "reverse links, avatar, domain mail"),
        ("4", "PHONE", "reverse lookup links"),
        ("5", "ADDRESS", "residents, maps, property pages"),
        ("6", "IMAGE", "EXIF metadata and public search links"),
        ("7", "WEBSIGHT", "names, email, phone, and address from one site"),
        ("8", "AUTO", "classify the query, then search"),
        ("9", "CATALOG", "source matrix and tiers"),
        ("L", "LAST", "reopen the previous report"),
        ("0", "EXIT", "close the session"),
    ]
    for key, name, blurb in items:
        menu.add_row(
            Text(key, style=g.ACCENT),
            Text(name, style=g.TEXT),
            Text(blurb, style=g.TEXT),
        )
    recent = Table.grid(padding=(0, 1))
    recent.add_column()
    if session.history:
        for item in session.history:
            recent.add_row(Text(" ▸ " + item, style=g.TEXT))
    else:
        recent.add_row(Text(" ▸ no searches yet", style=g.NAVY))
    stats = Text()
    stats.append(" SOURCES ", style=g.TEXT)
    stats.append(str(sources), style=g.ACCENT)
    stats.append("   LIVE PROBES ", style=g.TEXT)
    stats.append(str(probes), style=g.GREEN)
    stats.append("   CORE ", style=g.TEXT)
    stats.append(str(len(CORE_SOURCES)), style=g.ACCENT)
    body = Group(_session_line(session), Text(""), stats, Text(""), menu, Text(""), Text(" RECENT", style=g.ACCENT), recent)
    return Group(
        g.wordmark(),
        Text(""),
        _panel(body, " SELECT A LENS ", "public pages  ·  public APIs  ·  not a consumer report"),
    )


def render_catalog() -> RenderableType:
    from .models import Query

    samples = [
        ("name", Query("name", "Jane Doe", "Austin", "TX")),
        ("user", Query("username", "janedoe")),
        ("mail", Query("email", "jane@example.com")),
        ("phone", Query("phone", "4155550134")),
        ("addr", Query("address", "1 Main St", "Austin", "TX", "78701")),
    ]
    matrix: dict[str, dict[str, str]] = {}
    tiers: dict[str, str] = {}
    for label, query in samples:
        for row in build_links(query):
            matrix.setdefault(row.source, {})[label] = row.mode
            tiers[row.source] = row.tier
        for job in jobs_for(query):
            matrix.setdefault(job.source, {})[label] = "probe"
            tiers.setdefault(job.source, "live")
    table = Table(expand=True, box=box.SIMPLE_HEAD, header_style=g.ACCENT, border_style=g.NAVY)
    table.add_column("SOURCE", style=g.TEXT)
    for label, _query in samples:
        table.add_column(label.upper(), justify="center", width=6)
    table.add_column("TIER", width=8)
    for source in sorted(matrix, key=lambda name: (tiers.get(name, ""), name.lower())):
        cells: list[Text] = [Text(source)]
        for label, _query in samples:
            mode = matrix[source].get(label)
            if mode == "direct":
                cells.append(Text("●", style=g.GREEN))
            elif mode == "pivot":
                cells.append(Text("◐" if g.FANCY else "o", style=g.NAVY))
            elif mode == "probe":
                cells.append(Text("◉" if g.FANCY else "*", style=g.ACCENT))
            else:
                cells.append(Text("·", style=g.NAVY))
        tier = tiers.get(source, "")
        cells.append(Text(tier.upper(), style=g.tier_style(tier)))
        table.add_row(*cells)
    legend = Text()
    legend.append(" ● direct ", style=g.GREEN)
    legend.append(" ◐ indexed ", style=g.NAVY)
    legend.append(" ◉ live ", style=g.ACCENT)
    legend.append(" · none", style=g.TEXT)
    return _panel(Group(legend, table), " SOURCE MATRIX ")


def _fact_table(result: Result) -> Table:
    table = Table.grid(padding=(0, 1), expand=True)
    table.add_column(width=10)
    table.add_column()
    for label, value in result.query.facts():
        if result.query.kind in {"image", "websight"} and label == "QUERY":
            continue
        table.add_row(Text(label, style=g.TEXT), Text(value, style=g.TEXT))
    table.add_row(Text("TAG", style=g.TEXT), Text(result.tag, style=g.ACCENT))
    table.add_row(Text("ELAPSED", style=g.TEXT), Text(f"{result.elapsed_s:.2f}s", style=g.ACCENT))
    return table


def _coverage(rows: list[Row]) -> tuple[int, int, int]:
    core = [row for row in rows if row.core]
    direct = sum(1 for row in core if row.mode == "direct")
    pivot = sum(1 for row in core if row.mode == "pivot")
    return direct, pivot, len(CORE_SOURCES)


def _bars(rows: list[Row]) -> Table:
    visible = [row for row in rows if row.status != "skip"]
    counts = Counter(row.category for row in visible)
    top = max(counts.values()) if counts else 1
    table = Table.grid(padding=(0, 1))
    table.add_column(width=16)
    table.add_column(width=4, justify="right")
    table.add_column()
    for category in CAT_ORDER:
        count = counts.get(category, 0)
        if count == 0:
            continue
        table.add_row(
            Text(CAT_LABELS[category], style=g.cat_style(category)),
            Text(str(count), style=g.TEXT),
            g.meter(count / top, 16, g.cat_style(category)),
        )
    hits = sum(1 for row in visible if row.status == "hit")
    probes = sum(1 for row in visible if row.category == "live")
    if probes:
        table.add_row(
            Text("LIVE HITS", style=g.ACCENT),
            Text(f"{hits}/{probes}", style=g.TEXT),
            g.meter(hits / probes, 16, g.ACCENT),
        )
    return table


def _clip(text: str, limit: int = 42) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + ("…" if g.FANCY else ".")


def render_scan(
    query_label: str,
    rows_so_far: list[Row],
    total: int,
    phase: str,
    started: float,
    feed: list[Row],
    extra_done: int = 0,
) -> RenderableType:
    done = len([row for row in rows_so_far if row.status != "wait"]) + extra_done
    ratio = done / total if total else 1.0
    now = time.monotonic()
    header = Text()
    header.append(f" {g.clock(now)} ", style=g.ACCENT)
    header.append(phase, style=g.ACCENT)
    header.append("   ", style="")
    header.append(query_label, style=g.TEXT)
    header.append("\n ")
    header.append_text(g.gauge(ratio, 22))
    header.append(f"   {done}/{total}", style=g.TEXT)
    header.append(f"   {now - started:5.2f}s", style=g.ACCENT)
    feed_table = Table.grid(padding=(0, 1))
    feed_table.add_column(width=3)
    feed_table.add_column(width=20)
    feed_table.add_column(width=10)
    feed_table.add_column()
    shown = feed[-8:]
    if not shown:
        feed_table.add_row(Text("·", style=g.NAVY), Text("standing by", style=g.TEXT), Text(""), Text(""))
    for row in shown:
        feed_table.add_row(
            Text(g.status_glyph(row.status, now), style=g.status_style(row.status)),
            Text(row.source, style=g.TEXT),
            Text(row.status.upper(), style=g.status_style(row.status)),
            Text(_clip(row.detail, 36), style=g.TEXT),
        )
    return _panel(Group(header, Text(""), feed_table, Text(""), _bars(rows_so_far)), " SCAN ", "public sources")


def _status_cell(row: Row) -> Text:
    label = row.status.upper()
    if row.elapsed_ms is not None and row.category == "live":
        label += f" {row.elapsed_ms}ms"
    return Text(label, style=g.status_style(row.status))


def render_result(result: Result, interactive: bool = False) -> RenderableType:
    visible = result.visible()
    direct, pivot, total_core = _coverage(result.rows)
    header = Table.grid(expand=True)
    header.add_column(ratio=2)
    header.add_column(ratio=3)
    stats = Table.grid(padding=(0, 1))
    stats.add_column(width=14)
    stats.add_column()
    if result.query.kind == "image":
        meta_hits = sum(1 for row in visible if row.category == "meta" and row.status == "hit")
        stats.add_row(Text("METADATA", style=g.TEXT), Text(str(meta_hits), style=g.ACCENT))
    else:
        stats.add_row(Text("CORE DIRECT", style=g.TEXT), g.gauge(direct / total_core if total_core else 0, 14))
    link_count = sum(1 for row in visible if row.category != "live" and row.url)
    probe_rows = [row for row in visible if row.category == "live"]
    hits = [row for row in probe_rows if row.status == "hit"]
    stats.add_row(Text("LINKS", style=g.TEXT), Text(str(link_count), style=g.ACCENT))
    stats.add_row(
        Text("LIVE", style=g.TEXT),
        Text(f"{len(hits)} hit  /  {len(probe_rows)} checked", style=g.ACCENT if hits else g.TEXT),
    )
    if pivot:
        names = ", ".join(row.source for row in result.rows if row.core and row.mode == "pivot")
        stats.add_row(Text("INDEXED", style=g.TEXT), Text(names, style=g.NAVY))
    latencies = [float(row.elapsed_ms) for row in probe_rows if row.elapsed_ms is not None]
    if latencies:
        latency = Text()
        latency.append_text(g.sparkline(latencies, g.ACCENT))
        latency.append(f"  {int(min(latencies))}-{int(max(latencies))} ms", style=g.TEXT)
        stats.add_row(Text("LATENCY", style=g.TEXT), latency)
    header.add_row(_fact_table(result), Group(stats, Text(""), _bars(visible)))
    if result.query.kind in {"image", "websight"}:
        header = Group(Text(result.query.raw, style=g.TEXT), Text(""), header)

    summary = _summary(result)
    lines: list[RenderableType] = []
    last_category = ""
    number = 0
    for row in visible:
        if row.category != last_category:
            last_category = row.category
            if lines:
                lines.append(Text(""))
            lines.append(Text(" " + CAT_LABELS.get(row.category, row.category), style=g.cat_style(row.category)))
            lines.append(Text(" " + ("─" if g.FANCY else "-") * 28, style=g.NAVY))
        number += 1
        shown = row.source if len(row.source) <= 20 else row.source[:19] + ("…" if g.FANCY else ".")
        headline = Text()
        headline.append(f" {number:>3} ", style=g.ACCENT)
        headline.append(g.status_glyph(row.status) + " ", style=g.status_style(row.status))
        headline.append(f"{shown:<20} ", style=g.TEXT)
        headline.append(f"{row.tier.upper():<9}", style=g.tier_style(row.tier))
        headline.append_text(_status_cell(row))
        lines.append(headline)
        detail = " ".join(row.detail.split())
        generic = {
            "direct search page",
            "people search",
            "profile page",
            "web search",
            "video search",
            "post search",
            "user search",
            "map search",
            "article search",
            "company search",
            "party search",
            "filing search",
            "certificate search",
            "domain registration",
            "domain search page",
            "property search",
            "public breach notice page",
        }
        if detail and (row.category == "live" or detail not in generic):
            style = g.ACCENT if row.status == "hit" else g.TEXT
            lines.append(Text("      " + _clip(detail, 72), style=style))
        if row.url:
            lines.append(Text("      " + row.url, style=g.URL))
    notes = Text()
    notes.append(" ◆ link  ", style=g.NAVY)
    notes.append("● hit  ", style=g.ACCENT)
    notes.append("○ miss  ", style=g.RED)
    notes.append("! blocked  ", style=g.RED)
    notes.append("× error\n", style=g.RED)
    notes.append(
        " People-search rows open in the browser. Paid and preview sites may stop at a subscription wall.\n",
        style=g.TEXT,
    )
    notes.append(" ORION does not scrape those pages, solve checks, or pull private records.", style=g.TEXT)
    if result.query.kind == "image":
        notes.append(
            "\n Metadata is read on this machine. A local file is not uploaded.",
            style=g.TEXT,
        )
    if result.query.kind == "websight":
        notes.append(
            "\n WebSight reads public pages on the one site you entered.",
            style=g.TEXT,
        )
    if interactive:
        notes.append(
            "\n [O]pen #   [G]roup   [Y]ank urls   [E]xport   [N]ew   [B]ack   [Q]uit",
            style=g.ACCENT,
        )
    body: list[RenderableType] = [
        g.wordmark(),
        Text(""),
        _panel(header, " REPORT ", result.tag),
    ]
    if summary is not None:
        body.extend((Text(""), summary))
    body.extend(
        (
            Text(""),
            _panel(Group(*lines) if lines else Text(" No public sources for this query.", style=g.TEXT), " MESH "),
            Text(""),
            notes,
        )
    )
    return Group(*body)


def _summary(result: Result) -> RenderableType | None:
    if not result.preview or result.query.kind == "image":
        return None
    websight = result.query.kind == "websight"
    blocks: list[RenderableType] = []
    for index, card in enumerate(result.cards):
        if index:
            blocks.append(Text(""))
        block = Text()
        block.append(" " + card.source, style=g.ACCENT)
        block.append("\n " + card.title, style=g.TEXT)
        for label, value in card.fields:
            block.append(f"\n {label:<8} ", style=g.NAVY)
            limit = 320 if label in {"BIO", "ABOUT"} else 88
            block.append(_clip(value, limit), style=g.TEXT)
        if card.note:
            block.append("\n " + card.note, style=g.NAVY)
        if card.url:
            block.append("\n " + card.url, style=g.URL)
        blocks.append(block)
    if not blocks:
        if websight:
            blocks.append(Text(" No public page on this site returned contact details.", style=g.TEXT))
        else:
            blocks.append(
                Text(
                    " No public preview came back.\n"
                    " People-search sites do not return their pages here, so name, phone, and address listings stay in the links below.",
                    style=g.TEXT,
                )
            )
    blocks.append(Text(""))
    if websight:
        blocks.append(Text(" Read from the public pages on this one site.", style=g.NAVY))
        subtitle = "public pages on this site"
    else:
        blocks.append(Text(" A matching page is not proof it is the same person.", style=g.NAVY))
        subtitle = "public APIs  ·  directory sites stay as links"
    return _panel(Group(*blocks), " SUMMARY ", subtitle)


def numbered_rows(result: Result) -> list[Row]:
    return result.visible()
