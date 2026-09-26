"""FFmpeg operations; no shell interpolation, decoding, or video re-encoding."""
from __future__ import annotations

from collections import defaultdict
from fractions import Fraction
import json
import os
from pathlib import Path
import statistics
import subprocess

from .model import Chapter, MarkerError, chapters_equal, ffmetadata


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
            return json.loads(self.run([self.ffprobe, '-v', 'error', *args, '-of', 'json', str(path)]))
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
        duration = float(info['format']['duration'])
        if abs(float(source_info['format'].get('start_time', 0))) > 0.05:
            raise MarkerError('Source has a nonzero container start time; this timing convention is not supported yet')
        if abs(duration - (requested_end - requested_start)) > max_drift:
            raise MarkerError('Clip duration differs too much from the named segment; possible stale or merged export')
        first = self.packets(clip, 0, min(4, duration))
        source_first = self.packets(source, requested_start - max_drift,
                                    min(requested_start, max_drift) + max_drift + 6)
        offset, count = packet_offset(source_first, first)
        if abs(offset - requested_start) > max_drift:
            raise MarkerError('Matched clip start is too far from the requested segment start')
        # Check the tail too: the export must be one continuous, unchanged section.
        if duration > 6:
            tail = self.packets(clip, duration - 4, 5)
            if not tail:
                raise MarkerError('Cannot verify the end of this clip')
            first_tail_time = float(tail[0]['pts_time'])
            source_tail = self.packets(source, first_tail_time + offset - 2, duration - first_tail_time + 5)
            tail_offset, tail_count = packet_offset(source_tail, tail)
            if abs(tail_offset - offset) > 0.002:
                raise MarkerError('Beginning and end have different offsets; merged/retimed export is not supported')
            count += tail_count
        return offset, count

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


def packet_offset(source_packets: list[dict], clip_packets: list[dict]) -> tuple[float, int]:
    sources = defaultdict(list)
    clips = defaultdict(list)
    for packet in source_packets:
        if 'data_hash' in packet and 'pts_time' in packet:
            sources[packet['data_hash']].append(float(packet['pts_time']))
    for packet in clip_packets:
        if 'data_hash' in packet and 'pts_time' in packet:
            clips[packet['data_hash']].append(float(packet['pts_time']))
    offsets = [times[0] - clips[key][0] for key, times in sources.items()
               if len(times) == 1 and len(clips.get(key, [])) == 1]
    if len(offsets) < 5:
        raise MarkerError('Not enough unique matching video packets to verify timing (need 5); source, rename, or smart-cut mismatch')
    median = statistics.median(offsets)
    if max(abs(value - median) for value in offsets) > 0.002:
        raise MarkerError('Matching packets have inconsistent timestamps')
    return median, len(offsets)


def read_chapters(info: dict) -> list[Chapter]:
    return sorted([Chapter(float(c['start_time']), float(c['end_time']), c.get('tags', {}).get('title', ''))
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
