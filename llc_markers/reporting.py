"""Human-readable outcomes and next steps, alongside machine-readable reports."""
from collections import Counter


ACTIONS = {
    'missing_export': 'Export this saved segment, or use mapping_file if its export was renamed. Remove the saved segment only if it was intentionally discarded.',
    'unmatched': 'Find or restore the associated .llc project, check the filename prefix, or leave this clip outside the batch. A previously cleaned-up project may be in Project Backups.',
    'ambiguous': 'Use mapping_file to identify the exact export; each export must belong to only one saved segment.',
    'invalid_project': 'Open and save the project in LosslessCut, then check the reported error before rerunning.',
    'error': 'Resolve the reported verification or file-access error before retrying. Keep existing backups and any pending transaction record.',
}


def severity(status):
    if status in {'error', 'invalid_project'}:
        return 'error'
    return 'warning' if status in {'missing_export', 'unmatched', 'ambiguous', 'retained'} else 'info'


def summarize(report):
    counts = Counter(item['status'] for item in report['results'])
    cleanup = Counter(item['status'] for item in report['cleanup_results'])
    errors = sum(counts[s] for s in ('error', 'invalid_project')) + cleanup['error']
    warnings = (sum(counts[s] for s in ('missing_export', 'unmatched', 'ambiguous'))
                + len(report['unused_markers']) + cleanup['retained'])
    outcome = 'completed_with_errors' if errors else 'completed_with_attention' if warnings else 'completed'
    report.update(summary=dict(counts), cleanup_summary=dict(cleanup), error_count=errors,
                  warning_count=warnings, outcome=outcome, had_problems=bool(errors or warnings))
    report['marker_count'] = sum(len(x.get('markers', [])) for x in report['results']
                                 if x['status'] in {'updated', 'ready', 'up_to_date'})
    report['placeholder_count'] = sum(len(x.get('placeholder_chapters_filtered', [])) for x in report['results']
                                      if x['status'] in {'updated', 'ready'})


def summary_lines(report):
    mode = report['mode'].capitalize()
    ending = {'completed': 'complete.', 'completed_with_attention': 'complete with items needing attention.',
              'completed_with_errors': 'complete with errors; some items were not processed.'}[report['outcome']]
    counts, cleanup = report['summary'], report['cleanup_summary']
    lines = [f'{mode} {ending}',
             f"Scanned {report['project_count']} project(s) and {report['clip_count']} clip(s).",
             f"Clips: {counts.get('updated', 0)} updated; {counts.get('ready', 0)} ready to update; "
             f"{counts.get('up_to_date', 0)} already current; {counts.get('no_markers', 0)} with no new point markers.",
             f"Named point markers in verified plans: {report['marker_count']}; "
             f"placeholder chapters excluded from planned/written outputs: {report['placeholder_count']}."]
    if report['delete_projects_after_success']:
        lines.append(f"Projects: {cleanup.get('deleted', 0)} backed up and deleted; "
                     f"{cleanup.get('would_delete', 0)} eligible after Apply verification; "
                     f"{cleanup.get('retained', 0)} retained; {cleanup.get('error', 0)} cleanup error(s).")
    if report['had_problems']:
        lines.append(f"Attention: {counts.get('missing_export', 0)} missing export(s); "
                     f"{counts.get('unmatched', 0)} unmatched video(s); "
                     f"{counts.get('ambiguous', 0)} ambiguous match(es); "
                     f"{len(report['unused_markers'])} unused point marker(s); {report['error_count']} error(s).")
    lines.append(f"Elapsed: {report['elapsed_seconds']:.1f} seconds.")
    return lines


def render_text(report):
    lines = [f"LosslessCut marker {report['mode']} - {report['run_id']}", *summary_lines(report), '',
             f"Clips directory: {report['clips_dir']}", f"Projects directory: {report['projects_dir']}",
             f"Project deletion: {'enabled' if report['delete_projects_after_success'] else 'disabled'}",
             f"Placeholder cleanup: {'enabled' if report['remove_placeholder_chapters'] else 'disabled'}", '']
    issues = [x for x in report['results'] if x.get('severity') in {'warning', 'error'}]
    if issues or report['unused_markers']:
        lines.append('ITEMS NEEDING ATTENTION')
        for item in issues:
            lines.append(f"[{item['status']}] {item.get('clip', item.get('project', ''))}\n  {item['message']}")
            if item.get('segment_name'):
                lines.append(f"  Saved segment: {item['segment_name']}")
            if item.get('segment_key'):
                lines.append(f"  Mapping key: {item['segment_key']}")
            lines.append(f"  Next step: {item.get('action', '')}")
        for marker in report['unused_markers']:
            lines.append(f"[unused_marker] {marker['project']} @ {marker['source_seconds']:.3f}s - {marker['name']}")
            lines.append('  Next step: include this point in an exported cut, or remove it in LosslessCut if it is unwanted.')
        lines.append('')
    lines.append('ALL CLIP RESULTS')
    for item in report['results']:
        lines.append(f"[{item['status']}] {item.get('clip', item.get('project', ''))}\n  {item['message']}")
        if item.get('segment_key'):
            lines.append(f"  Mapping key: {item['segment_key']}")
        for marker in item.get('markers', []):
            lines.append(f"  Point marker: {marker['clip_seconds']:.3f}s - {marker['name']}")
        for chapter in item.get('placeholder_chapters_filtered', []):
            lines.append(f"  Excluded existing placeholder: {chapter['start']:.3f}s - {chapter['title']}")
        if item.get('backup'):
            lines.append(f"  Clip backup: {item['backup']}")
    if report['delete_projects_after_success']:
        lines.append(f"\nPROJECT CLEANUP: {report['cleanup_summary']}")
        for item in report['cleanup_results']:
            lines.append(f"[{item['status']}] {item['project']}\n  {item['message']}")
            if item.get('backup'):
                lines.append(f"  Project backup: {item['backup']}")
    lines.extend(['', f"JSON report: {report['report_path']}"])
    return '\n'.join(lines) + '\n'
