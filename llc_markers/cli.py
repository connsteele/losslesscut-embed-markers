from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import tomllib

from .engine import DEFAULT_WORK_DIR, Settings, process_batch
from .model import MarkerError


@contextmanager
def batch_lock(work_dir: Path):
    work_dir.mkdir(parents=True, exist_ok=True)
    lock = work_dir / 'run.lock'
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise MarkerError(f'Another run may be active. See {lock}. If the earlier process has stopped, remove this lock and retry.') from exc
    try:
        with os.fdopen(descriptor, 'w') as handle:
            json.dump({'pid':os.getpid()}, handle)
        yield
    finally:
        lock.unlink(missing_ok=True)


def parser():
    p = argparse.ArgumentParser(description='Embed saved LosslessCut point markers into exported clips. Preview is the default.')
    p.add_argument('mode', nargs='?', choices=['preview', 'apply'], default='preview')
    p.add_argument('--config', type=Path, default=Path('config.local.toml'))
    p.add_argument('--projects-dir', type=Path, help='Defaults to clips_dir when omitted from configuration')
    p.add_argument('--work-dir', type=Path, help='Defaults to .llc-markers-work inside the repository')
    for key in ('clips-dir','sources-dir','output-dir','mapping-file'):
        p.add_argument('--' + key, type=Path)
    p.add_argument('--prefix', help='Export filename prefix (default: "PRE ")')
    p.add_argument('--ffmpeg')
    p.add_argument('--ffprobe')
    p.add_argument('--delete-projects-after-success', action=argparse.BooleanOptionalAction,
                   default=None, help='Back up and delete fully verified .llc projects during Apply (default: off)')
    return p


def settings_from_args(args) -> Settings:
    values = {}
    config_base = Path.cwd()
    if args.config.is_file():
        values = tomllib.loads(args.config.read_text(encoding='utf-8-sig'))
        config_base = args.config.resolve().parent
    allowed = set(Settings.__dataclass_fields__)
    unknown = set(values) - allowed
    if unknown:
        raise MarkerError('Unknown configuration settings: ' + ', '.join(sorted(unknown)))
    paths = {'projects_dir','clips_dir','sources_dir','work_dir','mapping_file','output_dir'}
    for key in allowed:
        override = getattr(args, key, None)
        if override is not None:
            values[key] = override.resolve() if key in paths else override
        elif key in values and key in paths:
            values[key] = (config_base / Path(values[key])).resolve()
    required = {'clips_dir','sources_dir'}
    if not required.issubset(values):
        raise MarkerError('Create config.local.toml from config.example.toml, or supply --clips-dir and --sources-dir.')
    values.setdefault('projects_dir', values['clips_dir'])
    values.setdefault('work_dir', DEFAULT_WORK_DIR)
    return Settings(**values)


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='backslashreplace', line_buffering=True)
    args = parser().parse_args(argv)
    try:
        settings = settings_from_args(args)
        settings.validate()
        with batch_lock(settings.work_dir):
            report = process_batch(settings, apply=args.mode == 'apply')
        return 2 if report['had_problems'] else 0
    except KeyboardInterrupt:
        print('\nStopped. Completed clips stay recorded; check the latest report before rerunning.', file=sys.stderr)
        return 130
    except (MarkerError, OSError, ValueError, TypeError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1
