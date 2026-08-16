#!/usr/bin/env python3
"""Build and validate the committed NEVSISTER chart-to-video catalog.

The expensive channel inventory is cached below ``.local-data`` and is never
shipped to the browser.  The committed artifact contains only chart UUIDs and
canonical 11-character YouTube video IDs.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
CHANNEL_ID = "UCicVRsgv4iIhZGZcbx7xUkw"
UPLOADS_PLAYLIST_ID = "UUicVRsgv4iIhZGZcbx7xUkw"
CHANNEL_URL = f"https://www.youtube.com/channel/{CHANNEL_ID}/videos"
DEFAULT_CHARTS = ROOT / ".local-data/piu-scores/phoenix2/current/charts.json"
DEFAULT_CACHE = ROOT / ".local-data/nevsister/videos.json"
DEFAULT_OUTPUT = ROOT / "lib/data/nevsister-chart-videos.json"
DEFAULT_OVERRIDES = ROOT / "lib/data/nevsister-chart-video-overrides.json"
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
CHART_TOKEN_RE = re.compile(r"(?<![A-Z0-9])([SD])\s*[-_.]?\s*(\d{1,2})(?!\d)", re.I)
COOP_TOKEN_RE = re.compile(
    r"(?<![A-Z0-9])CO\s*[-_. ]?\s*OP\s*[-_. ]?\s*X\s*(\d)(?!\d)",
    re.I,
)


@dataclass(frozen=True)
class Chart:
    chart_id: str
    song_name: str
    mode: str
    level: int
    variant: str

    @property
    def difficulty(self) -> str:
        prefix = {"Single": "S", "Double": "D", "CoOp": "C"}[self.mode]
        return f"{prefix}{self.level}"

    @property
    def display_difficulty(self) -> str:
        return f"Co-op {self.level}x" if self.mode == "CoOp" else self.difficulty


@dataclass(frozen=True)
class Video:
    video_id: str
    title: str
    playlist_index: int


@dataclass(frozen=True)
class Candidate:
    video: Video
    matched_name: str
    rank: tuple[int, int, int, int]


def normalize(value: str) -> str:
    # Parentheses occasionally stylize a letter inside the English title
    # (F(R)IEND) rather than introduce a subtitle.
    value = re.sub(r"\(([A-Za-z0-9])\)", r"\1", value)
    # NEVSISTER inserts Korean translations directly after the English song
    # title. Remove only parentheticals containing non-ASCII text; English
    # qualifiers such as (GADGET mix) and (PIU Edit.) remain significant.
    value = re.sub(r"\([^)]*[^\x00-\x7f][^)]*\)", " ", value)
    value = unicodedata.normalize("NFKD", value).casefold()
    value = value.replace("&", " and ").replace("+", " plus ")
    value = "".join(character for character in value if not unicodedata.combining(character))
    return " ".join(re.findall(r"[^\W_]+", value, flags=re.UNICODE))


def chart_variant(value: str) -> str:
    normalized = normalize(value)
    if "short cut" in normalized or "shortcut" in normalized:
        return "short-cut"
    if "full song" in normalized or "fullsong" in normalized:
        return "full-song"
    return "normal"


def base_song_name(value: str) -> str:
    normalized = normalize(value)
    normalized = re.sub(r"\b(?:short\s*cut|full\s*song)\b", " ", normalized)
    return " ".join(normalized.split())


def core_song_names(value: str) -> list[str]:
    """Return conservative title aliases for routinely omitted suffixes."""
    name = base_song_name(value)
    cores = [name]
    stripped = re.sub(r"\s+(?:feat|ft)\b.*$", "", name).strip()
    stripped = re.sub(r"\s+(?:piu\s+edit|gadget\s+mix)$", "", stripped).strip()
    stripped = re.sub(r"\s+(?:overdoze|eurobeat\s+remix)$", "", stripped).strip()
    if stripped and stripped not in cores:
        cores.append(stripped)
    return cores


def explicit_difficulties(title: str) -> set[str]:
    standard = {
        f"{mode.upper()}{int(level)}"
        for mode, level in CHART_TOKEN_RE.findall(title)
    }
    coop = {f"C{int(players)}" for players in COOP_TOKEN_RE.findall(title)}
    return standard | coop


def load_charts(path: Path) -> list[Chart]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"Expected a chart array in {path}")

    charts: list[Chart] = []
    seen: set[str] = set()
    for item in raw:
        mode = str(item.get("type") or "")
        level = int(item.get("level") or 0)
        if mode not in {"Single", "Double", "CoOp"}:
            continue
        if mode != "CoOp" and level < 16:
            continue
        if mode == "CoOp" and level not in {2, 3, 4, 5}:
            continue
        chart_id = str(item.get("id") or "").strip()
        song_name = str(item.get("songName") or "").strip()
        if not chart_id or not song_name:
            raise ValueError(f"Chart is missing id/songName: {item!r}")
        if chart_id in seen:
            raise ValueError(f"Duplicate chart id: {chart_id}")
        seen.add(chart_id)
        charts.append(Chart(chart_id, song_name, mode, level, chart_variant(song_name)))
    return sorted(charts, key=lambda chart: chart.chart_id)


def _minimal_video(item: dict[str, Any], fallback_index: int) -> dict[str, Any] | None:
    video_id = str(item.get("id") or "")
    title = str(item.get("title") or "").strip()
    if not VIDEO_ID_RE.fullmatch(video_id) or not title:
        return None
    return {
        "id": video_id,
        "title": title,
        "playlistIndex": int(item.get("playlist_index") or fallback_index),
    }


def harvest_inventory(cache_path: Path, yt_dlp: str) -> list[Video]:
    command = [yt_dlp, "--flat-playlist", "--lazy-playlist", "--dump-json", CHANNEL_URL]
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
    )
    assert process.stdout is not None
    entries: list[dict[str, Any]] = []
    for index, line in enumerate(process.stdout, start=1):
        item = json.loads(line)
        playlist_channel_id = item.get("playlist_channel_id")
        if playlist_channel_id not in {None, CHANNEL_ID}:
            process.kill()
            raise RuntimeError(f"Unexpected channel id: {playlist_channel_id}")
        video = _minimal_video(item, index)
        if video:
            entries.append(video)
    _, stderr = process.communicate()
    if process.returncode:
        raise RuntimeError(f"yt-dlp failed ({process.returncode}):\n{stderr}")
    if len(entries) < 1_000:
        raise RuntimeError(f"Refusing suspiciously small channel inventory ({len(entries)} videos)")

    payload = {
        "schemaVersion": 1,
        "channelId": CHANNEL_ID,
        "source": CHANNEL_URL,
        "videos": entries,
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(cache_path)
    return videos_from_payload(payload, cache_path)


def videos_from_payload(payload: Any, path: Path) -> list[Video]:
    if not isinstance(payload, dict) or payload.get("channelId") != CHANNEL_ID:
        raise ValueError(f"Invalid or wrong-channel inventory: {path}")
    raw_videos = payload.get("videos")
    if not isinstance(raw_videos, list):
        raise ValueError(f"Invalid video array: {path}")
    videos: list[Video] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_videos, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Invalid video at position {index}")
        video_id = str(item.get("id") or "")
        title = str(item.get("title") or "").strip()
        if not VIDEO_ID_RE.fullmatch(video_id) or not title:
            raise ValueError(f"Invalid video at position {index}: {item!r}")
        if video_id in seen:
            continue
        seen.add(video_id)
        videos.append(Video(video_id, title, int(item.get("playlistIndex") or index)))
    return videos


def load_inventory(path: Path) -> list[Video]:
    return videos_from_payload(json.loads(path.read_text(encoding="utf-8")), path)


def load_overrides(path: Path) -> tuple[dict[str, list[str]], dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_aliases = payload.get("aliases", {})
    raw_charts = payload.get("charts", {})
    if not isinstance(raw_aliases, dict) or not isinstance(raw_charts, dict):
        raise ValueError(f"Invalid overrides: {path}")
    aliases: dict[str, list[str]] = {}
    for song_name, values in raw_aliases.items():
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise ValueError(f"Invalid aliases for {song_name!r}")
        aliases[base_song_name(song_name)] = [base_song_name(value) for value in values]
    chart_overrides = {str(key): str(value) for key, value in raw_charts.items()}
    return aliases, chart_overrides


def _contains_name(title: str, song: str) -> bool:
    return bool(song) and f" {song} " in f" {title} "


def _version_rank(title: str) -> int:
    if re.search(r"\bphoenix\s*2\b", title):
        return 5
    if re.search(r"\bphoenix\b", title):
        return 4
    if re.search(r"\b(?:piu\s*)?xx\b|double\s*x", title):
        return 3
    if re.search(r"\bprime\s*2\b", title):
        return 2
    if re.search(r"\bprime\b|fiesta|infinity", title):
        return 1
    return 0


def _dedicated_rank(title: str) -> int:
    score_or_play = re.search(
        r"\b(?:stage\s*pass|game\s*play|gameplay|full\s*combo|all\s*perfect|"
        r"\d+\s*miss|\d{2,3}(?:\.\d+)?\s*(?:sss|ss|aa|a)\+?)\b",
        title,
    )
    chart_header = re.search(r"(?:^|\[)\s*(?:pump\s*it\s*up|piu)\b", title)
    return 2 if chart_header and not score_or_play else (1 if not score_or_play else 0)


def candidate_rank(video: Video, matched_name: str, canonical_name: str) -> tuple[int, int, int, int]:
    title = normalize(video.title)
    exact_name = int(matched_name == canonical_name)
    # A lower playlist index is newer. It is an intentional final tiebreak for
    # corrected/rerated uploads of the same chart, not fuzzy ambiguity masking.
    return (_version_rank(title), _dedicated_rank(title), exact_name, -video.playlist_index)


def candidates_for_chart(
    chart: Chart, videos_by_difficulty: dict[str, list[Video]], aliases: dict[str, list[str]]
) -> list[Candidate]:
    canonical_name = base_song_name(chart.song_name)
    names = [*core_song_names(chart.song_name), *aliases.get(canonical_name, [])]
    candidates: list[Candidate] = []
    for video in videos_by_difficulty.get(chart.difficulty, []):
        if chart_variant(video.title) != chart.variant:
            continue
        title = normalize(video.title)
        for name in names:
            if _contains_name(title, name):
                candidates.append(Candidate(video, name, candidate_rank(video, name, canonical_name)))
                break
    return sorted(candidates, key=lambda candidate: candidate.rank, reverse=True)


def build_mapping(
    charts: list[Chart], videos: list[Video], aliases: dict[str, list[str]], overrides: dict[str, str]
) -> tuple[dict[str, str], list[Chart], dict[str, list[Candidate]]]:
    inventory_ids = {video.video_id for video in videos}
    videos_by_difficulty: dict[str, list[Video]] = {}
    for video in videos:
        for difficulty in explicit_difficulties(video.title):
            videos_by_difficulty.setdefault(difficulty, []).append(video)

    chart_ids = {chart.chart_id for chart in charts}
    unknown_overrides = sorted(set(overrides) - chart_ids)
    if unknown_overrides:
        raise ValueError(f"Overrides reference unknown charts: {', '.join(unknown_overrides)}")

    mapping: dict[str, str] = {}
    missing: list[Chart] = []
    ambiguous: dict[str, list[Candidate]] = {}
    for chart in charts:
        override = overrides.get(chart.chart_id)
        if override:
            if not VIDEO_ID_RE.fullmatch(override):
                raise ValueError(f"Invalid override video id for {chart.chart_id}: {override}")
            if override not in inventory_ids:
                raise ValueError(f"Override video is absent from channel inventory: {override}")
            mapping[chart.chart_id] = override
            continue

        candidates = candidates_for_chart(chart, videos_by_difficulty, aliases)
        if not candidates:
            missing.append(chart)
            continue
        # The playlist position resolves multiple uploads of the same chart. A
        # true ambiguity is two distinct candidates otherwise identical even
        # after this stable precedence, which should be manually overridden.
        top = candidates[0]
        tied = [candidate for candidate in candidates if candidate.rank == top.rank]
        if len({candidate.video.video_id for candidate in tied}) > 1:
            ambiguous[chart.chart_id] = tied
            continue
        mapping[chart.chart_id] = top.video.video_id
    return mapping, missing, ambiguous


def write_report(path: Path, missing: Iterable[Chart], ambiguous: dict[str, list[Candidate]]) -> None:
    payload = {
        "missing": [
            {
                "chartId": chart.chart_id,
                "songName": chart.song_name,
                "difficulty": chart.display_difficulty,
            }
            for chart in missing
        ],
        "ambiguous": {
            chart_id: [
                {"videoId": candidate.video.video_id, "title": candidate.video.title}
                for candidate in candidates
            ]
            for chart_id, candidates in ambiguous.items()
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_catalog(path: Path, charts: list[Chart], inventory: list[Video] | None = None) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schemaVersion") != 1 or payload.get("channelId") != CHANNEL_ID:
        raise ValueError(f"Invalid catalog metadata: {path}")
    mapping = payload.get("charts")
    if not isinstance(mapping, dict):
        raise ValueError(f"Invalid catalog chart mapping: {path}")
    expected_ids = {chart.chart_id for chart in charts}
    mapped_ids = set(mapping)
    missing = expected_ids - mapped_ids
    stale = mapped_ids - expected_ids
    invalid = {str(value) for value in mapping.values() if not VIDEO_ID_RE.fullmatch(str(value))}
    absent: set[str] = set()
    if inventory is not None:
        inventory_ids = {video.video_id for video in inventory}
        absent = {str(value) for value in mapping.values() if value not in inventory_ids}
    if missing or stale or invalid or absent:
        raise ValueError(
            "Catalog check failed: "
            f"missing={len(missing)}, stale={len(stale)}, invalid={len(invalid)}, absent={len(absent)}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--charts", type=Path, default=DEFAULT_CHARTS)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES)
    parser.add_argument("--report", type=Path, default=ROOT / ".local-data/nevsister/match-report.json")
    parser.add_argument("--yt-dlp", default="yt-dlp")
    parser.add_argument("--refresh", action="store_true", help="Refresh the ignored channel inventory cache")
    parser.add_argument("--check", action="store_true", help="Validate the committed mapping without rewriting it")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Do not require the ignored inventory cache when checking the committed mapping",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    charts = load_charts(args.charts)
    expected_singles = sum(chart.mode == "Single" for chart in charts)
    expected_doubles = sum(chart.mode == "Double" for chart in charts)
    expected_coop = sum(chart.mode == "CoOp" for chart in charts)
    print(
        f"Catalog charts: {len(charts)} "
        f"({expected_singles} Singles, {expected_doubles} Doubles, {expected_coop} Co-op)"
    )

    if args.check:
        inventory = None if args.offline else load_inventory(args.cache)
        validate_catalog(args.output, charts, inventory)
        print(f"OK: {len(charts)} chart mappings; missing=0, stale=0, invalid=0")
        return 0

    videos = harvest_inventory(args.cache, args.yt_dlp) if args.refresh or not args.cache.exists() else load_inventory(args.cache)
    print(f"NEVSISTER inventory: {len(videos)} videos")
    aliases, overrides = load_overrides(args.overrides)
    mapping, missing, ambiguous = build_mapping(charts, videos, aliases, overrides)
    write_report(args.report, missing, ambiguous)
    print(f"Matched={len(mapping)}, missing={len(missing)}, ambiguous={len(ambiguous)}")
    if missing or ambiguous:
        print(f"Review report: {args.report}", file=sys.stderr)
        return 1

    payload = {"schemaVersion": 1, "channelId": CHANNEL_ID, "charts": dict(sorted(mapping.items()))}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    validate_catalog(args.output, charts, videos)
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
