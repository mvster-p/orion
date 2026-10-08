"""Write a local markdown report of links and live checks."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .models import CAT_LABELS, Result

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def to_markdown(result: Result) -> str:
    lines = [
        f"# ORION report {result.tag}",
        "",
        f"- Type: {result.query.kind}",
        f"- Query: {result.query.raw}",
        f"- Place: {result.query.location or '-'}",
        f"- Elapsed: {result.elapsed_s:.2f}s",
        "",
        "Public search pages and public API checks. Not a consumer report.",
        "",
    ]
    if result.cards:
        lines.append("## Summary")
        lines.append("")
        for card in result.cards:
            lines.append(f"### {card.source} — {card.title}")
            lines.append("")
            for label, value in card.fields:
                lines.append(f"- {label}: {_cell(value)}")
            if card.note:
                lines.append(f"- Note: {_cell(card.note)}")
            if card.url:
                lines.append(f"- {card.url}")
            lines.append("")
        lines.append("A matching page is not proof it is the same person.")
        lines.append("")
    category = ""
    number = 0
    lines.append("| # | Source | Tier | Status | Detail | URL |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for row in result.visible():
        if row.category != category:
            category = row.category
            lines.append(f"|  | **{CAT_LABELS.get(category, category)}** |  |  |  |  |")
        number += 1
        lines.append(
            "| "
            + " | ".join(
                (
                    str(number),
                    _cell(row.source),
                    row.tier,
                    row.status,
                    _cell(row.detail),
                    row.url,
                )
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def save_report(result: Result) -> Path:
    REPORTS.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", result.query.raw).strip("-").lower()[:40] or "query"
    path = REPORTS / f"{stamp}-{result.query.kind}-{slug}.md"
    path.write_text(to_markdown(result), encoding="utf-8")
    return path
