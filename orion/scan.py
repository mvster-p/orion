"""Animated sweep over links and public probes."""

from __future__ import annotations

import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from rich.console import Console
from rich.live import Live

from .catalog import build_links
from .digest import card_jobs, order_cards, run_cards
from .models import Query, Result, Row, query_tag
from .probe import jobs_for, run_job
from .ui import render_scan


def _preview_row(source: str, status: str, detail: str) -> Row:
    return Row(
        source=source,
        category="live",
        tier="live",
        status=status,
        detail=detail,
        url="",
        mode="probe",
    )


def run_scan(console: Console, query: Query, probes: bool = True, animate: bool = True) -> Result:
    links = build_links(query)
    preview = bool(probes and query.kind != "image")
    jobs = jobs_for(query) if probes else []
    previews = card_jobs(query) if preview else []
    total = len(links) + len(jobs) + len(previews)
    started = time.perf_counter()
    revealed: list = []
    feed: list = []
    found_cards: list = []
    label = f"{query.kind.upper()}  {query.raw}"
    if query.location:
        label += f"  ·  {query.location}"

    def paint(phase: str, extra_done: int = 0) -> None:
        live.update(
            render_scan(label, revealed, total, phase, started, feed, extra_done),
            refresh=True,
        )

    if not animate:
        revealed.extend(links)
        feed.extend(links[-8:])
        if jobs or previews:
            with ThreadPoolExecutor(max_workers=6) as pool:
                row_futures = [pool.submit(run_job, job) for job in jobs]
                card_futures = [pool.submit(run_cards, fn) for _name, fn in previews]
                for future in row_futures:
                    row = future.result()
                    revealed.append(row)
                    feed.append(row)
                for future in card_futures:
                    found_cards.extend(future.result())
        return Result(
            query,
            revealed,
            time.perf_counter() - started,
            query_tag(query),
            cards=order_cards(found_cards),
            preview=preview,
        )

    with Live(console=console, refresh_per_second=12, transient=True) as live:
        paint("LINK FABRIC")
        for row in links:
            revealed.append(row)
            feed.append(row)
            paint("LINK FABRIC")
            time.sleep(0.02)
        if jobs or previews:
            phase = "LIVE MESH" if jobs else "PUBLIC PREVIEW"
            extra_done = 0
            with ThreadPoolExecutor(max_workers=6) as pool:
                pending: dict = {}
                for job in jobs:
                    pending[pool.submit(run_job, job)] = ("row", job.source)
                for name, fn in previews:
                    pending[pool.submit(run_cards, fn)] = ("card", name)
                try:
                    while pending:
                        done, _rest = wait(set(pending), timeout=0.08, return_when=FIRST_COMPLETED)
                        for future in done:
                            kind, source = pending.pop(future)
                            if kind == "row":
                                row = future.result()
                                revealed.append(row)
                                feed.append(row)
                            else:
                                cards = future.result()
                                extra_done += 1
                                if cards:
                                    found_cards.extend(cards)
                                    detail = cards[0].title if len(cards) == 1 else f"{len(cards)} records"
                                    feed.append(_preview_row(source, "hit", detail))
                                else:
                                    feed.append(_preview_row(source, "miss", "no public preview"))
                        waiting = [
                            _preview_row(pending[future][1], "wait", "in flight")
                            for future in list(pending)[:5]
                        ]
                        live.update(
                            render_scan(label, revealed, total, phase, started, feed + waiting, extra_done),
                            refresh=True,
                        )
                except KeyboardInterrupt:
                    pool.shutdown(wait=False, cancel_futures=True)
                    raise
            paint("COMPLETE", extra_done)
        else:
            paint("COMPLETE")
        time.sleep(0.18)
    return Result(
        query,
        revealed,
        time.perf_counter() - started,
        query_tag(query),
        cards=order_cards(found_cards),
        preview=preview,
    )
