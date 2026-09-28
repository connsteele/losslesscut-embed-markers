"""FFmpeg operations; no shell interpolation, decoding, or video re-encoding."""
from __future__ import annotations

from collections import defaultdict
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import statistics
import subprocess

from .model import Chapter, MarkerError, chapters_equal, ffmetadata


class InsufficientPacketMatches(MarkerError):
    """A larger sample may resolve repeated/static encoded video packets."""


class MediaTools:
    def __init__(self, ffmpeg: str, ffprobe: str, temp_dir: Path):
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.env = dict(os.environ, TEMP=str(temp_dir), TMP=str(temp_dir), TMPDIR=str(temp_dir))

    def run(self, args: list[str], timeout: int = 120) -> str:
        try:
            result = subprocess.run(args, capture_output=True, text=True, encoding='utf-8',
                                    errors='replace', timeout=timeout, env=self.env, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise MarkerError(f'Could not run {Path(args[0]).name}: {exc}') from exc
        if result.returncode:
            raise MarkerError(f'{Path(args[0]).name} failed: {result.stderr.strip()[-3000:]}')
        return result.stdout

    def probe(self, path: Path, *args) -> dict:
        try:
            data = json.loads(self.run([self.ffprobe, '-v', 'error', *args, '-of', 'json', str(path)]))
            if not isinstance(data, dict):
                raise ValueError('Expected an object')
            return data
        except ValueError as exc:
            raise MarkerError('FFprobe returned invalid JSON') from exc

    def info(self, path: Path) -> dict:
        return self.probe(path, '-show_format', '-show_streams', '-show_chapters')

    def packets(self, path: Path, start: float, length: float) -> list[dict]:
        return self.probe(path, '-select_streams', 'v:0', '-read_intervals', f'{max(0,start):.6f}%+{length:.6f}',
                          '-show_packets', '-show_data_hash', 'sha256',
                          '-show_entries', 'packet=pts_time,data_hash').get('packets', [])

    def alignment(self, source: Path, clip: Path, requested_start: float, requested_end: float,
                  info: dict, source_info: dict, max_drift: float = 8) -> tuple[float, int]:
        duration = finite_time(info['format']['duration'])
        if duration <= 0:
            raise MarkerError('Clip duration must be positive')
        if abs(finite_time(source_info['format'].get('start_time', 0))) > 0.05:
            raise MarkerError('Source has a nonzero container start time; this timing convention is not supported yet')
        if abs(duration - (requested_end - requested_start)) > max_drift:
            raise MarkerError('Clip duration differs too much from the named segment; possible stale or merged export')
        offset, count = self.sample_alignment(source, clip, requested_start, duration, max_drift)
        if abs(offset - requested_start) > max_drift:
            raise MarkerError('Matched clip start is too far from the requested segment start')
        # Check the tail too: the export must be one continuous, unchanged section.
        if duration > 6:
            tail_offset, tail_count = self.sample_alignment(source, clip, offset, duration, max_drift, tail=True)
            if abs(tail_offset - offset) > 0.002:
                raise MarkerError('Beginning and end have different offsets; merged/retimed export is not supported')
            count += tail_count
        return offset, count

    def sample_alignment(self, source: Path, clip: Path, expected_offset: float,
                         duration: float, max_drift: float, *, tail: bool = False) -> tuple[float, int]:
        # Static AV1 footage may repeat every packet in a four-second sample.
        # Enlarge the evidence window, never lower the uniqueness requirement or
        # retry contradictory evidence. Keep source searches near the saved cut.
        last_error = None
        previous_window = None
        for size in (4, 8, 16, 32):
            window = min(size, duration)
            if window == previous_window:
                break
            previous_window = window
            start = max(0, duration - window) if tail else 0
            packets = self.packets(clip, start, window + (1 if tail else 0))
            if not packets:
                raise MarkerError(f'Cannot verify the {"end" if tail else "beginning"} of this clip: no video packets')
            times = [finite_time(p['pts_time']) for p in packets if 'pts_time' in p]
            if not times:
                raise MarkerError('Video packets have no presentation timestamps')
            first_time, last_time = min(times), max(times)
            source_start = max(0, expected_offset + first_time - max_drift)
            source_end = expected_offset + last_time + max_drift + 2
            # Absolute end avoids shortening the search when seeking lands on an
            # earlier keyframe. FFprobe may legitimately return that preroll.
            source_packets = self.probe(source, '-select_streams', 'v:0', '-read_intervals',
                                        f'{source_start:.6f}%{source_end:.6f}',
                                        '-show_packets', '-show_data_hash', 'sha256',
                                        '-show_entries', 'packet=pts_time,data_hash').get('packets', [])
            try:
                return packet_offset(source_packets, packets)
            except InsufficientPacketMatches as exc:
                last_error = exc
        raise InsufficientPacketMatches(
            f'Cannot verify {"end" if tail else "beginning"} after sampling up to {previous_window:g}s: '
            f'{last_error}. Repeated/static video, a different source, or a re-encoded export can cause this; '
            'check the source and saved cut, then re-export without smart cut if needed.')

    def stream_hashes(self, path: Path) -> str:
        return self.run([self.ffmpeg, '-hide_banner', '-loglevel', 'error', '-i', str(path),
                         '-map', '0:v?', '-map', '0:a?', '-c', 'copy', '-f', 'streamhash', '-hash', 'sha256', '-'],
                        timeout=7200).strip()

    def remux(self, clip: Path, output: Path, chapters: list[Chapter], input_info: dict) -> None:
        meta = output.with_suffix('.ffmetadata')
        meta.write_text(ffmetadata(chapters), encoding='utf-8')
        args = [self.ffmpeg, '-hide_banner', '-loglevel', 'error', '-n', '-i', str(clip),
                '-f', 'ffmetadata', '-i', str(meta)]
        for stream in content_streams(input_info):
            args.extend(['-map', f"0:{stream['index']}"])
        args.extend(['-map_metadata', '0', '-map_chapters', '1', '-c', 'copy', str(output)])
        self.run(args, timeout=7200)
        self.verify(clip, output, chapters, input_info)

    def verify(self, clip: Path, output: Path, chapters: list[Chapter], input_info: dict) -> None:
        actual = self.info(output)
        if not chapters_equal(chapters, read_chapters(actual)):
            raise MarkerError('Written chapter positions or labels differ from the plan')
        if stream_signatures(input_info) != stream_signatures(actual):
            raise MarkerError('An audio, video, subtitle or other content stream changed')
        if abs(float(input_info['format']['duration']) - float(actual['format']['duration'])) > 0.05:
            raise MarkerError('The remux changed the clip duration')
        if self.stream_hashes(clip) != self.stream_hashes(output):
            raise MarkerError('Encoded audio/video hashes differ; refusing to replace the clip')


def finite_time(value) -> float:
    try:
        result = float(value)
        if isinstance(value, bool) or not math.isfinite(result):
            raise ValueError()
        return result
    except (TypeError, ValueError, OverflowError) as exc:
        raise MarkerError(f'Invalid media timestamp: {value!r}') from exc


def packet_offset(source_packets: list[dict], clip_packets: list[dict]) -> tuple[float, int]:
    sources = defaultdict(list)
    clips = defaultdict(list)
    for packet in source_packets:
        if 'data_hash' in packet and 'pts_time' in packet:
            sources[packet['data_hash']].append(finite_time(packet['pts_time']))
    for packet in clip_packets:
        if 'data_hash' in packet and 'pts_time' in packet:
            clips[packet['data_hash']].append(finite_time(packet['pts_time']))
    offsets = [times[0] - clips[key][0] for key, times in sources.items()
               if len(times) == 1 and len(clips.get(key, [])) == 1]
    median = statistics.median(offsets) if offsets else 0
    if offsets and max(abs(value - median) for value in offsets) > 0.002:
        raise MarkerError('Matching packets have inconsistent timestamps')
    if len(offsets) < 5:
        raise InsufficientPacketMatches(f'Found {len(offsets)} unique matching video packets; need at least 5')
    return median, len(offsets)


def read_chapters(info: dict) -> list[Chapter]:
    return sorted([Chapter(finite_time(c['start_time']), finite_time(c['end_time']), c.get('tags', {}).get('title', ''))
                   for c in info.get('chapters', [])], key=lambda c: c.start)


def content_streams(info: dict) -> list[dict]:
    streams = []
    for stream in info.get('streams', []):
        # FFmpeg exposes the QuickTime chapter track as bin_data/text. Rebuild it
        # from metadata; copying it as well would leave obsolete chapter text.
        chapter_track = (bool(info.get('chapters')) and stream.get('codec_type') == 'data'
                         and stream.get('codec_tag_string') == 'text')
        if not chapter_track:
            streams.append(stream)
    return streams


def stream_signatures(info: dict) -> list[dict]:
    keys = ('codec_type', 'codec_name', 'width', 'height', 'sample_rate', 'channels', 'pix_fmt')
    return [{k: s.get(k) for k in keys if k in s} for s in content_streams(info)]


def video_fps(info: dict) -> float:
    video = next((s for s in info.get('streams', []) if s.get('codec_type') == 'video'
                  and not s.get('disposition', {}).get('attached_pic')), None)
    if not video:
        raise MarkerError('Clip has no video track')
    try:
        fps = float(Fraction(video['r_frame_rate']))
        if fps <= 0:
            raise ValueError()
        return fps
    except (ValueError, ZeroDivisionError, KeyError) as exc:
        raise MarkerError('Cannot determine the clip frame rate') from exc
