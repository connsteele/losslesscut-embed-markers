"""Batch planning, safe commits, and repeat-run bookkeeping."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid

from .media import MediaTools, read_chapters, video_fps
from .model import (Chapter, MarkerError, build_chapters, chapter_dicts, chapters_equal,
                    filename_matches, marker_chapters, markers_for_segment, read_project)

VIDEO_SUFFIXES = {'.mp4', '.mov', '.mkv'}
PROBLEM_STATUSES = {'error', 'unmatched', 'missing_export', 'ambiguous', 'invalid_project'}
DEFAULT_WORK_DIR = Path(__file__).resolve().parent.parent / '.llc-markers-work'


@dataclass
class Settings:
    projects_dir: Path
    clips_dir: Path
    sources_dir: Path
    work_dir: Path
    prefix: str = 'PRE '
    ffmpeg: str = 'ffmpeg'
    ffprobe: str = 'ffprobe'
    mapping_file: Path | None = None
    output_dir: Path | None = None
    delete_projects_after_success: bool = False

    def validate(self):
        if not isinstance(self.delete_projects_after_success, bool):
            raise MarkerError('delete_projects_after_success must be true or false (without quotes)')
        for name in ('projects_dir', 'clips_dir', 'sources_dir'):
            path = getattr(self, name)
            if not path.is_dir():
                raise MarkerError(f'{name} is not a directory: {path}')
        for path in (self.work_dir, self.output_dir):
            if path and (path == self.clips_dir or self.clips_dir.is_relative_to(path)):
                raise MarkerError('Work/output directory must not contain the clips directory')
        if self.output_dir and (self.output_dir == self.work_dir or self.output_dir.is_relative_to(self.work_dir)
                                or self.work_dir.is_relative_to(self.output_dir)):
            raise MarkerError('Output and work directories must be separate')
        for name in ('ffmpeg', 'ffprobe'):
            if not shutil.which(getattr(self, name)):
                raise MarkerError(f'{name} was not found. Install FFmpeg or configure its full executable path.')


def signature(path: Path) -> dict:
    stat = path.stat()
    return {'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}


def file_hash(path: Path) -> str:
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def state_path(settings: Settings, destination: Path) -> Path:
    key = hashlib.sha256(os.path.normcase(str(destination.resolve())).encode()).hexdigest()
    return settings.work_dir / 'state' / (key + '.json')


def load_state(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if data.get('version') != 1:
            raise ValueError('Unrecognized state version')
        return data
    except (ValueError, OSError) as exc:
        raise MarkerError(f'Cannot read processing state {path}: {exc}') from exc


def files_under(root: Path, suffixes: set[str], exclude: tuple[Path, ...] = ()) -> list[Path]:
    results = []
    for current, dirs, files in os.walk(root, followlinks=False):
        folder = Path(current)
        dirs[:] = [name for name in dirs if not (folder / name).is_symlink()
                   and not any((folder / name).resolve().is_relative_to(e) for e in exclude)]
        for name in files:
            path = folder / name
            if path.suffix.casefold() in suffixes and not path.is_symlink():
                if not any(path.resolve().is_relative_to(e) for e in exclude):
                    results.append(path.resolve())
    return sorted(results)


def match_clip(project, segment, settings, clips, mappings):
    key = project.path.relative_to(settings.projects_dir).as_posix() + f'::{segment.index}'
    if key in mappings:
        path = (settings.clips_dir / mappings[key]).resolve()
        if not path.is_relative_to(settings.clips_dir) or path not in clips:
            raise MarkerError(f'Explicit mapping {key} must name an existing clip inside clips_dir')
        return key, [path]
    return key, [p for p in clips if filename_matches(segment.name, p.name, settings.prefix)]


def find_source(project, source_index, clip):
    # Prefer a source beside its project, then a unique recording in sources_dir.
    adjacent = (project.path.parent / project.source_name).resolve()
    if adjacent.is_file() and adjacent != clip:
        return adjacent
    candidates = [p for p in source_index[Path(project.source_name).name.casefold()] if p != clip]
    if len(candidates) != 1:
        raise MarkerError(f'Expected one original recording {project.source_name!r}; found {len(candidates)}')
    return candidates[0]


def base_chapters(current: list[Chapter], state: dict | None) -> list[Chapter]:
    if not state:
        return current
    saved = [Chapter(**c) for c in state['chapters']]
    if chapters_equal(current, saved):
        return [Chapter(**c) for c in state['base_chapters']]
    if not current:
        # Re-exported from LosslessCut: no old embedded annotations remain.
        return []
    raise MarkerError('Existing chapters changed since the last run. Keep this file unchanged and review its state/report.')


def install_result(settings, clip, output, destination, original_signature, record, record_path, run_id):
    if signature(clip) != original_signature:
        raise MarkerError('Input clip changed during processing; replacement cancelled')
    expected_destination = signature(destination) if destination.exists() else None
    backup = None
    if destination.exists():
        backup = settings.work_dir / 'backups' / run_id / destination.relative_to(
            settings.output_dir if settings.output_dir else settings.clips_dir)
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(destination, backup)
        if file_hash(destination) != file_hash(backup):
            raise MarkerError('Backup verification failed')
    record['backup'] = str(backup) if backup else None
    record['destination'] = str(destination)
    record['status'] = 'prepared'
    pending = record_path.with_suffix('.pending.json')
    write_json(pending, record)
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = destination.with_name('.llc-markers-' + uuid.uuid4().hex + destination.suffix)
    try:
        # Cross-drive remux output is copied to the destination volume before the
        # final atomic replace. A failed copy never replaces the original clip.
        shutil.copy2(output, stage)
        if file_hash(stage) != file_hash(output):
            raise MarkerError('Final copy verification failed')
        if signature(clip) != original_signature:
            raise MarkerError('Input clip changed before commit')
        actual_destination = signature(destination) if destination.exists() else None
        if actual_destination != expected_destination:
            raise MarkerError('Destination changed before commit')
        stat = clip.stat()
        os.utime(stage, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        record['output_signature'] = signature(stage)
        record['output_sha256'] = file_hash(stage)
        write_json(pending, record)
        os.replace(stage, destination)
        record['status'] = 'complete'
        write_json(record_path, record)
        pending.unlink()
    finally:
        stage.unlink(missing_ok=True)
    return backup


@dataclass
class CleanupEvidence:
    files: dict[Path, dict]
    media: Path
    chapters: list[Chapter]
    sha256: str

    def check_signatures(self):
        for path, expected in self.files.items():
            if signature(path) != expected:
                raise MarkerError(f'File changed after verification; project retained: {path}')


def cleanup_projects(settings, paths, parsed_projects, project_signatures, project_hashes,
                     evidence, report, report_path, tools, apply, emit):
    """Remove only complete projects, with a verified backup and a durable receipt."""
    by_project = defaultdict(list)
    for result in report['results']:
        by_project[result.get('project')].append(result)
    unused = {m['project'] for m in report['unused_markers']}
    allowed = {'updated', 'up_to_date', 'no_markers'}
    if not apply:
        allowed.add('ready')

    for path in paths:
        item = {'project': str(path), 'status': 'retained', 'message': ''}
        report['cleanup_results'].append(item)
        try:
            project = parsed_projects.get(path)
            results = by_project[str(path)]
            problems = sorted({r['status'] for r in results if r['status'] not in allowed})
            reasons = []
            if project is None or problems:
                reasons.append('Project has unresolved results: ' + ', '.join(problems or ['invalid_project']))
            if project is not None:
                keys = [path.relative_to(settings.projects_dir).as_posix() + f'::{s.index}'
                        for s in project.segments]
                if not keys:
                    reasons.append('Project has no saved cut segments to verify')
                if Counter(r.get('segment_key') for r in results) != Counter(keys):
                    reasons.append('Not every saved segment has exactly one completed result')
                if any(key not in evidence for key in keys):
                    reasons.append('Not every export passed verification')
            if str(path) in unused:
                reasons.append('Point markers exist outside the saved cut segments')
            if reasons:
                item['message'] = '; '.join(reasons)
            else:
                def check_project():
                    if path.is_symlink() or not path.resolve().is_relative_to(settings.projects_dir):
                        raise MarkerError('Project path changed or is outside projects_dir; cleanup cancelled')
                    if (signature(path) != project_signatures[path]
                            or file_hash(path) != project_hashes[path]):
                        raise MarkerError('Project changed since scanning; cleanup cancelled')

                check_project()
                verified = [evidence[key] for key in keys]
                emit(f'[cleanup checking] {path.name}: checking all {len(verified)} export(s)...')
                for entry in verified:
                    entry.check_signatures()
                    if file_hash(entry.media) != entry.sha256:
                        raise MarkerError(f'Export content changed after verification: {entry.media}')
                    if not chapters_equal(read_chapters(tools.info(entry.media)), entry.chapters):
                        raise MarkerError(f'Export marker labels or positions changed: {entry.media}')
                for entry in verified:
                    entry.check_signatures()
                check_project()
                if not apply:
                    item.update(status='would_delete', message=(
                        'Would back up and delete after Apply completes all required writes and verification'))
                else:
                    backup = (settings.work_dir / 'Project Backups' / report['run_id']
                              / path.relative_to(settings.projects_dir))
                    item['backup'] = str(backup)
                    item['sha256'] = project_hashes[path]
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    with path.open('rb') as source, backup.open('xb') as target:
                        shutil.copyfileobj(source, target)
                    shutil.copystat(path, backup)
                    if file_hash(backup) != project_hashes[path]:
                        raise MarkerError('Project backup verification failed; cleanup cancelled')
                    # Persist the backup location before deleting, so an interrupted
                    # run can always be reconciled using its JSON report.
                    item.update(status='prepared', message='Backup verified; project deletion pending')
                    write_json(report_path, report)
                    for entry in verified:
                        entry.check_signatures()
                    check_project()
                    path.unlink()
                    item.update(status='deleted', message='Project deleted after verification; backup retained')
        except (MarkerError, OSError, ValueError, KeyError) as exc:
            item.update(status='error', message=str(exc))
        emit(f"[cleanup {item['status']}] {path}: {item['message']}")
        write_json(report_path, report)


def process_batch(settings: Settings, apply: bool = False, emit=print) -> dict:
    settings.validate()
    settings.work_dir.mkdir(parents=True, exist_ok=True)
    temp_root = settings.work_dir / 'temp'
    temp_root.mkdir(exist_ok=True)
    tools = MediaTools(settings.ffmpeg, settings.ffprobe, temp_root)
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:6]
    report_path = settings.work_dir / 'reports' / (run_id + '.json')
    report = {'version': 1, 'run_id': run_id, 'mode': 'apply' if apply else 'preview',
              'projects_dir': str(settings.projects_dir), 'clips_dir': str(settings.clips_dir),
              'report_path': str(report_path), 'results': [], 'unused_markers': [],
              'delete_projects_after_success': settings.delete_projects_after_success,
              'cleanup_results': []}

    def report_item(status, **details):
        item = dict(status=status, **details)
        report['results'].append(item)
        emit(f"[{status}] {details.get('clip', details.get('project', ''))}: {details.get('message', '')}")
        write_json(report_path, report)
        return item

    exclusions = tuple(p for p in (settings.work_dir, settings.output_dir) if p)
    projects = files_under(settings.projects_dir, {'.llc'}, exclusions)
    clips = files_under(settings.clips_dir, VIDEO_SUFFIXES, exclusions)
    source_index = defaultdict(list)
    for source in files_under(settings.sources_dir, VIDEO_SUFFIXES, exclusions):
        source_index[source.name.casefold()].append(source)
    mappings = {}
    if settings.mapping_file:
        mappings = json.loads(settings.mapping_file.read_text(encoding='utf-8-sig'))
        if not isinstance(mappings, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in mappings.items()):
            raise MarkerError('Mapping file must contain an object mapping segment keys to relative clip paths')
    jobs = []
    project_signatures = {}
    project_hashes = {}
    parsed_projects = {}
    cleanup_evidence = {}
    claimed = defaultdict(list)
    considered = set()
    for path in projects:
        try:
            project_signatures[path] = signature(path)
            if settings.delete_projects_after_success:
                project_hashes[path] = file_hash(path)
            project = read_project(path)
            if (signature(path) != project_signatures[path]
                    or (settings.delete_projects_after_success and file_hash(path) != project_hashes[path])):
                raise MarkerError('Project changed while being read; save it and rerun')
            parsed_projects[path] = project
            for marker in project.markers:
                if not any(s.start <= marker.start < s.end for s in project.segments):
                    report['unused_markers'].append({'project': str(path), 'name': marker.name, 'source_seconds': marker.start})
            for segment in project.segments:
                key, matches = match_clip(project, segment, settings, clips, mappings)
                considered.update(matches)
                if len(matches) != 1:
                    status = 'ambiguous' if matches else 'missing_export'
                    report_item(status, project=str(path), segment_key=key, message=f'{segment.name!r}: {len(matches)} matching clips')
                    continue
                job = (project, segment, matches[0], key)
                jobs.append(job)
                claimed[matches[0]].append(key)
        except (MarkerError, OSError, ValueError) as exc:
            report_item('invalid_project', project=str(path), message=str(exc))

    for clip in clips:
        if clip not in considered:
            report_item('unmatched', clip=str(clip), message='No saved segment matches this export; file left unchanged')
    source_infos = {}
    for project, segment, clip, key in jobs:
        details = {'project': str(project.path), 'segment_key': key, 'clip': str(clip)}
        try:
            if len(claimed[clip]) != 1:
                report_item('ambiguous', **details, message='Multiple saved segments claim this clip: ' + ', '.join(claimed[clip]))
                continue
            destination = (settings.output_dir / clip.relative_to(settings.clips_dir)) if settings.output_dir else clip
            record_path = state_path(settings, destination)
            if record_path.with_suffix('.pending.json').exists():
                raise MarkerError(f'Interrupted commit recorded at {record_path.with_suffix(".pending.json")}; review recovery instructions')
            state = load_state(record_path)
            markers = markers_for_segment(project, segment)
            if not markers and not state and not settings.delete_projects_after_success:
                report_item('no_markers', **details, message='No point markers in this segment; no media write needed')
                continue
            before = signature(clip)
            info = tools.info(clip)
            duration = float(info['format']['duration'])
            fps = video_fps(info)
            source = find_source(project, source_index, clip)
            source_before = signature(source)
            project_before = project_signatures[project.path]
            if signature(project.path) != project_before:
                raise MarkerError('Project changed since batch scanning; save it and rerun')
            if source not in source_infos or source_infos[source][0] != source_before:
                source_infos[source] = (source_before, tools.info(source))
            offset, matched_packets = tools.alignment(source, clip, segment.start, segment.end, info, source_infos[source][1])
            current = read_chapters(info)

            def remember_verification(media, chapters, media_signature, sha256=None):
                if not settings.delete_projects_after_success:
                    return
                watched = {source: source_before, project.path: project_before, media: media_signature}
                if media != clip:
                    watched[clip] = before
                entry = CleanupEvidence(watched, media, chapters, sha256 or file_hash(media))
                entry.check_signatures()
                cleanup_evidence[key] = entry

            if not markers and not state:
                # Cleanup still needs proof that an unmarked export belongs to
                # its saved segment. It remains untouched, even in copy mode.
                remember_verification(clip, current, before)
                report_item('no_markers', **details, source=str(source), destination=str(clip),
                            offset_seconds=offset, matched_packets=matched_packets,
                            message='No point markers; export timing verified for project cleanup')
                continue
            base = base_chapters(current, state if not settings.output_dir else None)
            desired = build_chapters(base, marker_chapters(markers, offset, duration, fps), duration, clip.suffix)
            details.update(destination=str(destination), source=str(source), offset_seconds=offset,
                           matched_packets=matched_packets,
                           markers=[{'name':m.name, 'source_seconds':m.start, 'clip_seconds':m.start-offset} for m in markers],
                           chapters=chapter_dicts(desired))
            destination_before = signature(destination) if destination.exists() else None
            if destination.exists() and destination != clip:
                if not state:
                    raise MarkerError('Output already exists without a processing record; refusing to overwrite')
                if signature(destination) != state['output_signature']:
                    raise MarkerError('Previously generated output changed; refusing to overwrite')
                output_chapters = read_chapters(tools.info(destination))
                same_input = state.get('input_signature') == before
                up_to_date = same_input and chapters_equal(output_chapters, desired)
            else:
                up_to_date = destination == clip and chapters_equal(current, desired)
            if up_to_date:
                if settings.delete_projects_after_success:
                    if destination != clip:
                        tools.verify(clip, destination, desired, info)
                    # Previously written media must still match its verified
                    # receipt; a matching chapter list alone is insufficient.
                    expected_hash = state['output_sha256'] if state else None
                    if expected_hash and file_hash(destination) != expected_hash:
                        raise MarkerError('Previously generated export content changed; project retained')
                    remember_verification(destination, desired, destination_before, expected_hash)
                report_item('up_to_date', **details, message='Embedded chapters already match; no rewrite needed')
                continue
            if not apply:
                remember_verification(clip, current, before)
                report_item('ready', **details, message=f'{len(markers)} point marker(s); {len(desired)} total embedded chapter(s)')
                continue
            emit(f'[processing] {clip.name}: writing and checking audio/video and chapter labels...')
            with tempfile.TemporaryDirectory(prefix='remux-', dir=temp_root) as temporary:
                output = Path(temporary) / ('marked' + clip.suffix)
                tools.remux(clip, output, desired, info)
                if signature(source) != source_before or signature(project.path) != project_before:
                    raise MarkerError('Source or project changed during processing; try again after saving/exporting')
                record = {'version':1, 'run_id':run_id, 'input':str(clip), 'input_signature':before,
                          'project':str(project.path), 'segment_key':key, 'source':str(source),
                          'offset_seconds':offset, 'base_chapters':chapter_dicts(base), 'chapters':chapter_dicts(desired)}
                backup = install_result(settings, clip, output, destination, before, record, record_path, run_id)
            remember_verification(destination, desired, record['output_signature'], record['output_sha256'])
            report_item('updated', **details, backup=str(backup) if backup else None, message=f'Embedded {len(markers)} point marker(s); verified unchanged audio/video')
        except (MarkerError, OSError, ValueError, KeyError) as exc:
            report_item('error', **details, message=str(exc))
    if settings.delete_projects_after_success:
        cleanup_projects(settings, projects, parsed_projects, project_signatures, project_hashes,
                         cleanup_evidence, report, report_path, tools, apply, emit)
    report['summary'] = dict(Counter(item['status'] for item in report['results']))
    report['cleanup_summary'] = dict(Counter(item['status'] for item in report['cleanup_results']))
    report['project_count'] = len(projects)
    report['clip_count'] = len(clips)
    report['had_problems'] = (any(item['status'] in PROBLEM_STATUSES for item in report['results'])
                              or any(item['status'] in {'retained', 'error'} for item in report['cleanup_results']))
    write_json(report_path, report)
    text_path = report_path.with_suffix('.txt')
    lines = [f"LosslessCut marker {report['mode']} — {run_id}", f'Projects: {len(projects)}; clips: {len(clips)}',
             ', '.join(f'{k}: {v}' for k,v in report['summary'].items()), '']
    for item in report['results']:
        lines.append(f"[{item['status']}] {item.get('clip', item.get('project',''))}\n  {item['message']}")
        if item.get('segment_key'):
            lines.append(f"  Mapping key: {item['segment_key']}")
        for marker in item.get('markers', []):
            lines.append(f"  {marker['clip_seconds']:.3f}s — {marker['name']}")
    if report['unused_markers']:
        lines.append('\nUnused markers (outside every saved segment):')
        lines.extend(f"  {m['project']} @ {m['source_seconds']:.3f}s — {m['name']}" for m in report['unused_markers'])
    if settings.delete_projects_after_success:
        lines.append(f"\nProject cleanup: {report['cleanup_summary']}")
        for item in report['cleanup_results']:
            lines.append(f"[{item['status']}] {item['project']}\n  {item['message']}")
            if item.get('backup'):
                lines.append(f"  Project backup: {item['backup']}")
    text_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    emit(f"Summary: {report['summary']}\nReport: {text_path}")
    if settings.delete_projects_after_success:
        emit(f"Project cleanup: {report['cleanup_summary']}")
    return report
