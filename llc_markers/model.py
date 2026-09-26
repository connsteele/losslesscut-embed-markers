"""Read JSON5 projects and translate annotations into chapter metadata."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from pathlib import Path
import re
import unicodedata

import json5


class MarkerError(Exception):
    """A problem that should be reported without guessing or changing this clip."""


@dataclass(frozen=True)
class Marker:
    start: float
    name: str


@dataclass(frozen=True)
class Segment:
    index: int
    start: float
    end: float
    name: str


@dataclass(frozen=True)
class Project:
    path: Path
    source_name: str
    segments: list[Segment]
    markers: list[Marker]


@dataclass(frozen=True)
class Chapter:
    start: float
    end: float
    title: str


def number(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MarkerError(f'{label} must be a number')
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise MarkerError(f'{label} must be finite and non-negative')
    return value


def read_project(path: Path) -> Project:
    try:
        data = json5.loads(path.read_text(encoding='utf-8-sig'), allow_duplicate_keys=False)
        if not isinstance(data, dict) or data.get('version') != 2:
            raise MarkerError('Only LosslessCut project version 2 is supported')
        source_name = data['mediaFileName']
        if not isinstance(source_name, str) or not source_name.strip():
            raise MarkerError('Missing source recording name')
        if not isinstance(data['cutSegments'], list):
            raise MarkerError('cutSegments must be a list')
        segments, markers = [], []
        for i, item in enumerate(data['cutSegments']):
            start = number(item['start'], f'Entry {i} start')
            name = item.get('name', '')
            if not isinstance(name, str):
                raise MarkerError(f'Entry {i} name must be text')
            if item.get('end') is None:
                if not name.strip():
                    raise MarkerError(f'Point marker {i} has no label; name it in LosslessCut')
                markers.append(Marker(start, name))
            else:
                end = number(item['end'], f'Entry {i} end')
                if end <= start:
                    raise MarkerError(f'Segment {i} has an invalid range')
                segments.append(Segment(i, start, end, name))
        # Selection controls export in LosslessCut, not ownership of saved annotations.
        # The actual presence of an exported file determines whether we process it.
        return Project(path, source_name, segments, sorted(markers, key=lambda m: m.start))
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise MarkerError(f'Cannot read {path.name}: {exc}') from exc


def normalized_name(value: str) -> str:
    value = unicodedata.normalize('NFC', value).casefold()
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', value)
    return re.sub(r'[\s_-]+', ' ', value).strip(' .')


def filename_matches(label: str, filename: str, prefix: str) -> bool:
    if not label.strip():
        return False
    return normalized_name(Path(filename).stem) in {
        normalized_name(label), normalized_name(prefix + label)
    }


def markers_for_segment(project: Project, segment: Segment) -> list[Marker]:
    return [m for m in project.markers if segment.start <= m.start < segment.end]


def marker_chapters(markers: list[Marker], offset: float, duration: float, fps: float) -> list[Chapter]:
    chapters = []
    for marker in markers:
        position = marker.start - offset
        if position < -0.002 or position >= duration:
            raise MarkerError(f'Marker {marker.name!r} falls outside the actual exported clip')
        position = max(0.0, position)
        chapters.append(Chapter(position, min(duration, position + 1 / fps), marker.name))
    return chapters


def build_chapters(base: list[Chapter], markers: list[Chapter], duration: float, suffix: str) -> list[Chapter]:
    # MP4 chapter tracks have millisecond precision. Resolve exposes one marker per
    # position. Combine colliding labels deliberately, rather than dropping one.
    groups: dict[int, list[Chapter]] = {}
    for chapter in [*base, *markers]:
        if not 0 <= chapter.start < duration:
            raise MarkerError('An existing chapter lies outside the clip duration')
        groups.setdefault(round(chapter.start * 1000), []).append(chapter)
    combined = []
    for _, group in sorted(groups.items()):
        labels = list(dict.fromkeys(c.title for c in group))
        combined.append(Chapter(min(c.start for c in group), min(duration, max(c.end for c in group)), ' | '.join(labels)))
    if suffix.casefold() in {'.mp4', '.mov'} and combined:
        if round(combined[0].start * 1000) != 0:
            combined.insert(0, Chapter(0, combined[0].start, 'Clip start'))
        # FFmpeg's QuickTime chapter track needs contiguous chapters starting at 0.
        # A lone later chapter otherwise gets silently moved to time zero.
        combined = [Chapter(round(c.start * 1000) / 1000,
                            round(combined[i + 1].start * 1000) / 1000 if i + 1 < len(combined) else duration,
                            c.title) for i, c in enumerate(combined)]
    return combined


def chapters_equal(a: list[Chapter], b: list[Chapter], *, ends: bool = True) -> bool:
    return len(a) == len(b) and all(
        x.title == y.title and abs(x.start - y.start) <= 0.002
        and (not ends or abs(x.end - y.end) <= 0.002)
        for x, y in zip(a, b)
    )


def ffmetadata(chapters: list[Chapter]) -> str:
    def escape(text):
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        return ''.join('\\' + c if c in '\\=;#\n' else c for c in text)
    lines = [';FFMETADATA1']
    for c in chapters:
        start, end = round(c.start * 1_000_000), round(c.end * 1_000_000)
        if end <= start:
            raise MarkerError('Chapter duration is too short to represent')
        lines.extend(['[CHAPTER]', 'TIMEBASE=1/1000000', f'START={start}', f'END={end}', f'title={escape(c.title)}'])
    return '\n'.join(lines) + '\n'


def chapter_dicts(chapters: list[Chapter]) -> list[dict]:
    return [asdict(c) for c in chapters]
