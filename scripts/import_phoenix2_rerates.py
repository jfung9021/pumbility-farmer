#!/usr/bin/env python3
"""Import Phoenix-to-Phoenix-2 rerates from the community workbook."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import posixpath
import re
import unicodedata
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARCHIVE = ROOT / "public" / "data" / "phoenix1-20260807.json"
DEFAULT_OUTPUT = ROOT / "public" / "data" / "phoenix1-rerates-20260807.json"
SHEET_NAME = "Phoenix 2 build"
MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
RATING_RE = re.compile(r"([SD])\s*(\d{1,2})", re.IGNORECASE)
TITLE_ALIASES = {
    "Halloween Party": "Halloween Party ~Multiverse~",
    "Stardream": "Stardream (feat. Romelon)",
    "End of the World": "The End of the World ft. Skizzo",
    "Utsushiyo no Kaze": "Utsushiyo No Kaze feat. Kana",
    "Ignis Fatuus": "Ignis Fatuus(DM Ashura Mix)",
    "FAEP 2-X": "Final Audition Ep. 2-X",
    "FAEP 2-2": "Final Audition Ep. 2-2",
    "Human Extinction": "Human Extinction (PIU Edit.)",
    "Underworld": "Underworld ft. Skizzo (PIU Edit.)",
    "FAEP 2-X shortcut": "Final Audition EP. 2-X - SHORT CUT -",
    "Dignity full": "Dignity - FULL SONG -",
    "LIADZ 2 full": "Love is a Danger Zone 2 - FULL SONG -",
    "Beat of the War 2 full": "Beat of the War 2 - FULL SONG -",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _normalize_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).casefold()
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = normalized.replace("&", "and")
    return "".join(char for char in normalized if char.isalnum())


def _shared_strings(book: zipfile.ZipFile) -> list[str]:
    try:
        root = ElementTree.fromstring(book.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return [
        "".join(node.text or "" for node in item.findall(f".//{{{MAIN_NS}}}t"))
        for item in root.findall(f"{{{MAIN_NS}}}si")
    ]


def _sheet_path(book: zipfile.ZipFile, sheet_name: str) -> str:
    workbook = ElementTree.fromstring(book.read("xl/workbook.xml"))
    relation_id = None
    for sheet in workbook.findall(f".//{{{MAIN_NS}}}sheet"):
        if sheet.attrib.get("name") == sheet_name:
            relation_id = sheet.attrib.get(f"{{{REL_NS}}}id")
            break
    if not relation_id:
        raise ValueError(f"Workbook has no {sheet_name!r} worksheet.")

    relationships = ElementTree.fromstring(book.read("xl/_rels/workbook.xml.rels"))
    for relation in relationships.findall(f"{{{PKG_REL_NS}}}Relationship"):
        if relation.attrib.get("Id") == relation_id:
            target = relation.attrib["Target"].lstrip("/")
            return posixpath.normpath(posixpath.join("xl", target))
    raise ValueError(f"Workbook relationship for {sheet_name!r} is missing.")


def _cell_text(cell: ElementTree.Element, shared: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(
            node.text or "" for node in cell.findall(f".//{{{MAIN_NS}}}t")
        ).strip()
    value = cell.find(f"{{{MAIN_NS}}}v")
    if value is None or value.text is None:
        return ""
    if cell_type == "s":
        return shared[int(value.text)].strip()
    return value.text.strip()


def read_rerate_rows(path: Path) -> list[dict[str, Any]]:
    with zipfile.ZipFile(path) as book:
        shared = _shared_strings(book)
        worksheet = ElementTree.fromstring(book.read(_sheet_path(book, SHEET_NAME)))
    rows: list[dict[str, Any]] = []
    for row in worksheet.findall(f".//{{{MAIN_NS}}}row"):
        number = int(row.attrib["r"])
        if number == 1:
            continue
        values: dict[str, str] = {}
        for cell in row.findall(f"{{{MAIN_NS}}}c"):
            reference = cell.attrib.get("r", "")
            column = re.match(r"[A-Z]+", reference)
            if column and column.group() in {"A", "B", "C", "D"}:
                values[column.group()] = _cell_text(cell, shared)
        if values.get("A"):
            rows.append(
                {
                    "sourceRow": number,
                    "song": values.get("A", ""),
                    "fromText": values.get("B", ""),
                    "toText": values.get("C", ""),
                    "notes": values.get("D", ""),
                }
            )
    return rows


def _rating_pairs(row: dict[str, Any]) -> list[tuple[str, str, int]]:
    old = [(mode.upper(), int(level)) for mode, level in RATING_RE.findall(row["fromText"])]
    new = [(mode.upper(), int(level)) for mode, level in RATING_RE.findall(row["toText"])]
    if not old or len(old) != len(new):
        return []
    pairs: list[tuple[str, str, int]] = []
    for (old_mode, old_level), (new_mode, new_level) in zip(old, new):
        if old_mode != new_mode or old_level == new_level:
            continue
        pairs.append(
            (f"{old_mode}{old_level}", f"{new_mode}{new_level}", new_level - old_level)
        )
    return pairs


def build_rerate_payload(workbook_path: Path, archive_path: Path) -> dict[str, Any]:
    archive = json.loads(archive_path.read_text(encoding="utf-8"))
    charts = [*archive.get("singles", []), *archive.get("doubles", [])]
    chart_index: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for chart in charts:
        key = (_normalize_title(str(chart["songName"])), str(chart["difficulty"]).upper())
        chart_index.setdefault(key, []).append(chart)

    rerates: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    seen_chart_ids: set[str] = set()
    for row in read_rerate_rows(workbook_path):
        for old_rating, new_rating, delta in _rating_pairs(row):
            if int(old_rating[1:]) < 20:
                continue
            archive_title = TITLE_ALIASES.get(row["song"], row["song"])
            key = (_normalize_title(archive_title), old_rating)
            candidates = chart_index.get(key, [])
            if len(candidates) != 1:
                same_rating = [
                    chart for chart in charts if str(chart["difficulty"]).upper() == old_rating
                ]
                suggestions = difflib.get_close_matches(
                    row["song"],
                    [str(chart["songName"]) for chart in same_rating],
                    n=3,
                    cutoff=0.45,
                )
                unmatched.append({**row, "from": old_rating, "to": new_rating, "suggestions": suggestions})
                continue
            chart = candidates[0]
            chart_id = str(chart["chartId"])
            if chart_id in seen_chart_ids:
                raise ValueError(f"Spreadsheet maps chart {chart_id} more than once.")
            seen_chart_ids.add(chart_id)
            rerates.append(
                {
                    "chartId": chart_id,
                    "from": old_rating,
                    "to": new_rating,
                    "delta": delta,
                    "direction": "uprated" if delta > 0 else "downrated",
                    "sourceRow": row["sourceRow"],
                }
            )

    if unmatched:
        details = "\n".join(
            f"row {item['sourceRow']}: {item['song']} {item['from']} -> {item['to']} "
            f"(suggestions: {', '.join(item['suggestions']) or 'none'})"
            for item in unmatched
        )
        raise ValueError(f"Could not match {len(unmatched)} level-20+ rerates:\n{details}")

    rerates.sort(key=lambda item: (item["sourceRow"], item["chartId"]))
    return {
        "schemaVersion": 1,
        "source": {
            "workbook": workbook_path.name,
            "worksheet": SHEET_NAME,
            "sha256": _sha256(workbook_path),
        },
        "phoenix1ArchiveSha256": _sha256(archive_path),
        "rerates": rerates,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = build_rerate_payload(args.workbook.resolve(), args.archive.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Imported {len(payload['rerates'])} Phoenix 1 chart rerates to {args.output}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
