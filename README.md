# LosslessCut Embed Markers

Batch-embed named **point markers** from LosslessCut `.llc` projects into exported video clips, so DaVinci Resolve can read their labels and positions. Keep using segments to cut your clips and point markers to annotate moments inside them.

The tool matches each saved segment to an existing export, verifies its timing against the original recording, and writes chapters by **copying the existing streams without re-encoding**. It does not cut, rename, organize, or transcode your footage.

## Requirements

These are **not bundled**:

- **Python 3.11 or newer**, available as `python` in your terminal. On Windows, enable the Python installer's PATH option. [Python downloads](https://www.python.org/downloads/)
- **FFmpeg and FFprobe**, available on PATH, or configured using their full executable paths. Use a recent build with support for your recording codecs. [FFmpeg downloads](https://ffmpeg.org/download.html)
- The Python **json5** package, installed automatically by setup. LosslessCut projects are JSON5, not ordinary JSON.
- Saved **LosslessCut version 2 projects**, their exported clips, and the **original recordings**. The originals verify timing after keyframe-aligned cuts.
- Free space in the work directory for temporary marked clips and retained backups, plus space beside the destination for the final temporary copy.

LosslessCut and Resolve do not need to be running. Finish saving projects and exporting the batch before processing.

### Resolve compatibility

A real LosslessCut 3.69.0 export (AV1 video + FLAC audio) was tested in **DaVinci Resolve Studio 21.1 on Windows**:

- MP4 chapter labels appeared as named clip markers and carried onto the timeline. The label was also verified visually in Resolve.
- MKV chapters were recognized as named clip markers and carried onto the timeline.
- Audio and video stream hashes matched the originals.

**MP4/MOV behavior:** the FFmpeg QuickTime chapter track must start at zero. If the first annotation occurs later, this tool inserts a `Clip start` chapter at zero. A naive export containing only a later chapter was observed to move it to zero; this tool avoids that failure. MKV does not require the extra marker. Output keeps the input container/extension; there is no automatic format conversion.

Chapter titles supply marker names. LosslessCut colors and arbitrary tags are not transferred. Project-wide text search behavior and other Resolve versions are not yet covered by the compatibility test. Import/reimport the marked file after processing; automatic refresh of an already-imported clip is not guaranteed.

## Windows quick start

1. Install Python and FFmpeg as described above.
2. Double-click **`setup.cmd`**. It creates a repository-local `.venv`, installs the Python package, copies the example configuration if a local one does not exist, and checks FFmpeg and FFprobe on PATH. Internet access is needed for the initial Python dependencies.
3. Edit **`config.local.toml`** with your export and original recording directories. Projects default to the export directory; working files default to `.llc-markers-work` inside the repo.
4. Double-click **`run.cmd`** and choose **1 — Preview**.
5. Read the results or text report. Once the matches look correct, run it again and choose **2 — Apply**.

The launcher processes the configured batch once; it does not prompt for each clip or watch folders. Local configuration, the virtual environment, and media are excluded from Git.

The menu also offers **3 — Open the latest readable report**, **4 — Reset setup**, and **5 — Exit**. Reset removes only `.venv`, generated package metadata, and known Python cache directories. It preserves `config.local.toml`, footage, mapping files, and all working data, including backups, reports, and processing state. Run `setup.cmd` afterward to rebuild the environment with your existing configuration. Reset remains accessible when `.venv` is missing, using Python on PATH for its checks.

Reset refuses to proceed if a run lock exists, a configured media/work directory overlaps a reset target, or a target contains linked files/directories. Finish active processing first. The reset operates only on the repository containing the launcher; it does not uninstall Python or FFmpeg or change Windows settings. Use this option instead of `git clean -fdX`, which would also erase ignored recovery data. Reset deliberately keeps your configuration; to change its settings, edit `config.local.toml` using the example as a reference.

Setup reports each media tool separately, showing its location when found and confirming it can run. If either tool is missing or fails to start, setup prints installation and PATH instructions and exits with code 1; the Python environment and configuration remain available. Install an FFmpeg build containing both executables, add their folder to Windows PATH, then reopen setup from a new terminal or Explorer window. You can instead configure full executable paths in `config.local.toml` for processing; setup's check only examines PATH.

On machines with `G:\GPT\Temp` and `G:\GPT\Caches`, setup uses those existing folders for process temporary files and pip downloads. It does not change Windows-wide settings. Processing uses the configured work directory for temporary media.

## Configuration

Example `config.local.toml`:

```toml
clips_dir = "I:/Video/Cut"
sources_dir = "I:/Video/Unsorted"
prefix = "PRE "
remove_placeholder_chapters = false
delete_projects_after_success = false
```

Use forward slashes in TOML paths. Relative paths are resolved relative to the configuration file; command-line path overrides are relative to your current directory.

Only `clips_dir` and `sources_dir` are required. The prefix defaults to `PRE ` if omitted. FFmpeg and FFprobe default to executables on PATH. Explicit `projects_dir` and `work_dir` values continue to override the defaults.

The example configuration places the required folders first, followed by optional filename matching, output location, cleanup behavior, dependency paths, and working storage. Settings remain flat TOML keys; the sections are comments, so existing configurations remain compatible.

The default working directory is anchored to the repository's location, not your terminal's current directory. It contains temporary files plus persistent `backups`, `Project Backups`, `reports`, and `state` folders. Temporary remux files are cleaned up automatically; preserve the other folders between runs. `.llc-markers-work/` is excluded from Git. You can still set `work_dir` to another drive for more space. If you change it, copy your existing state, both backup folders, and reports to the new location before processing again.

| Setting | Meaning |
| --- | --- |
| `clips_dir` | Recursively search here for exported `.mp4`, `.mov`, and `.mkv` clips. |
| `sources_dir` | Recursively search for originals named by the projects. Originals are read only. |
| `prefix` | Optional export filename prefix; default `PRE `. Both exact and prefixed labels are accepted. |
| `projects_dir` | Optional: recursively search here for `.llc` files. Defaults to the resolved `clips_dir`. |
| `mapping_file` | Optional JSON file providing exact associations when names no longer match. |
| `output_dir` | Optional: create modified copies instead of replacing exports. Relative subfolders are preserved. Clips needing no changes are not copied. |
| `remove_placeholder_chapters` | Optional boolean, default `false`: exclude existing chapters named exactly `Start` or `Unnamed N` while preserving incoming `.llc` point markers. Preview lists exclusions. |
| `delete_projects_after_success` | Optional boolean, default `false`: during Apply, back up and delete each `.llc` only after all its exports pass verification. Preview only reports eligibility. |
| `ffmpeg`, `ffprobe` | Executable names on PATH or full executable paths. |
| `work_dir` | Optional: temporary media, state, reports, and backups. Defaults to the repo's `.llc-markers-work`. |

Work/output directories are excluded from scans. Symbolic links are not followed. Each destination must match one segment; ambiguous associations are reported and left unchanged.

### Matching renamed clips

Matching ignores case and differences between spaces, underscores, and hyphens, and accommodates characters sanitized by LosslessCut. It does **not** guess from partial or similar names. Timing verification provides a second check before writing.

For an unnamed segment or an export you renamed, configure an exact mapping:

```toml
mapping_file = "mapping.local.json"
```

```json
{
  "source-proj.llc::0": "PRE My renamed clip.mp4",
  "subfolder/another-proj.llc::2": "Narrative/Another clip.mp4"
}
```

Keys are the project's path relative to `projects_dir`, using `/`, followed by `::` and its **zero-based index in `cutSegments`**. This index includes point-marker entries, not only segments. Reports print the key for each segment. Values are paths relative to `clips_dir` and must remain inside it.

## Command-line use

From the repository directory, after setup:

```powershell
# Preview is the default; media is not changed.
.\.venv\Scripts\python.exe -m llc_markers

# Apply, with backups of replaced exports.
.\.venv\Scripts\python.exe -m llc_markers apply

# Test marked copies, preserving input clips and projects even if cleanup is configured.
.\.venv\Scripts\python.exe -m llc_markers apply --output-dir "I:\Video\Marked Copies" --no-delete-projects-after-success

# Another configuration, or an individual directory override.
.\.venv\Scripts\python.exe -m llc_markers preview --config "another-batch.toml"
.\.venv\Scripts\python.exe -m llc_markers preview --clips-dir "I:\Video\Cut"

.\.venv\Scripts\python.exe -m llc_markers --help
```

On other platforms, create/activate a virtual environment and run `python -m pip install -e .`, then `python -m llc_markers`. The Python implementation is portable; launchers and the live Resolve compatibility test are Windows-specific.

## Processing rules

1. Parse each JSON5 project. Entries with an end time are cut segments; entries without one are point markers.
2. Match saved segment labels to actual exports. Export presence controls processing; changing `selected` in LosslessCut does not discard saved annotations.
3. Include markers from segment start (inclusive) to end (exclusive). A shared-boundary marker belongs to the segment starting there; overlapping segments can both receive it.
4. Compare unique encoded video packet hashes and timestamps near the export's beginning and end against the original. Use this verified offset instead of assuming the requested cut start was exact. At least five unique matches are required at each checked end.
5. Preserve unrelated chapters. Combine labels at the same millisecond using ` | ` instead of dropping one.
6. Write a temporary file using stream copy. Verify chapter labels/times, content stream properties, duration, and SHA-256 hashes of encoded audio/video.
7. Back up an existing destination and verify the backup. Copy the result to a temporary file on the destination volume, verify the copy, then replace the destination atomically. Preserve the export's modification time.
8. Record the result and continue. A clip failure does not stop unrelated clips.
9. If project cleanup is enabled, check every project independently at the end of the batch, verify its backup, and delete only eligible `.llc` files.

Clips without point markers are normally untouched. Exceptions are placeholder cleanup when enabled, and a previously processed clip whose annotations were subsequently removed: its managed chapters are removed while original unrelated chapters are restored according to the placeholder setting.

### Optional placeholder-chapter cleanup

Set `remove_placeholder_chapters = true` to exclude existing chapters whose entire title is `Start` or `Unnamed N`, where N contains digits. Matching ignores capitalization and surrounding whitespace. It preserves other titles such as `Start here`, `Unnamed hero`, and `Clip start`. This setting filters the pre-existing chapter list only: an incoming `.llc` point marker you deliberately named `Start` or `Unnamed 1` is still embedded.

Preview lists each excluded placeholder's title and position. Apply uses the same stream-copy verification and backups as any other chapter update. This can modify a clip with zero point markers, including creating a separate copy when `output_dir` is set. MP4/MOV may still need a `Clip start` chapter at zero; that format requirement remains.

The original chapter baseline is kept in processing state. Turning this setting back off and applying again restores its preserved placeholders, provided the matching project, clip path, and state remain available. For projects already removed by project cleanup, restore their `.llc` backups first. Unmatched clips are not changed by this option.

### Repeat runs and edits

Keep `work_dir/state` between runs. It records which chapters the tool created and which existed beforehand. Unchanged clips are skipped. Editing a point marker's text or position in the `.llc` updates its chapter on the next apply; removing annotations removes corresponding managed chapters. Chapters altered outside the tool are reported for review rather than overwritten.

Process markers **before filing/renaming exports** where possible. State is associated with the destination path. If you move a processed export and later want to update/remove its annotations, its processing record must be migrated too; automatic state migration is not implemented yet.

### Optional project cleanup

To clear completed LosslessCut projects from the working bin, set this in `config.local.toml`:

```toml
delete_projects_after_success = true
```

Run **Preview** first to see `would_delete` candidates and reasons for retaining other projects. Preview never deletes projects or creates project backups. A candidate means the saved segments and current exports passed the planning checks; Apply must still complete any required marker writes and verify the results before deletion. Set the option back to `false` to keep all projects. CLI overrides are `--delete-projects-after-success` and `--no-delete-projects-after-success`.

Cleanup runs after clip processing, independently for each project. A missing or failed export keeps its entire project, while unrelated completed projects can be cleaned up. Eligibility requires:

- At least one saved cut segment, with exactly one verified export for every segment, including unselected segments saved in the project.
- Every point marker inside an exported segment and present in the resulting clip at its verified position. Markers outside all saved segments prevent deletion.
- Successful audio/video and chapter verification for rewritten clips. Existing processed outputs must still match their recorded checksum. Already processed copies are also checked against the current input clip.
- No pending media transaction, ambiguous match, project error, or failed verification for that project.
- Unchanged project content and unchanged verified files through the final cleanup checks.

**Zero-marker segments qualify too.** With cleanup enabled, their exports are checked against the original recording even though no media write is needed. Projects containing only these segments can be cleaned up. With cleanup disabled, the existing fast skip remains. Cleanup adds media reads and therefore takes longer.

Before deleting, the tool copies the exact `.llc` to `work_dir/Project Backups/<run-id>/<relative-project-path>`, verifies its SHA-256 checksum, and records that location in the JSON report. With the default settings, this is inside the repo's gitignored `.llc-markers-work/Project Backups/`. Backup or deletion errors are reported and do not stop cleanup of unrelated projects. Only saved `.llc` files are eligible; source recordings and other companion files are preserved.

With `output_dir`, modified copies must pass verification before the project is removed. Exports needing no changes stay in `clips_dir` and are verified there; they are not copied. A clip with no point markers can still need a copy if placeholder cleanup changes its chapters. Finish saving and close projects before running cleanup so LosslessCut does not save changes or recreate a deleted project during the run.

To revise cuts or markers later, copy the recorded project backup back to its original path, set cleanup to `false`, and keep the existing processing state. Avoid overwriting a newer project when restoring. Exports left in the scan folder after project deletion can be reported as `unmatched` on later runs; file completed clips or restore their projects if you need to process them again.

## Reports and recovery

Completed runs produce JSON and readable text reports under `work_dir/reports`. JSON progress is saved after each reported clip, preserving partial results after an interruption. The console shows the active cleanup settings, clip progress, a readable outcome, counts by result type, and elapsed time. Text reports lead with the summary and an **Items needing attention** section containing suggested next steps and mapping keys. Detailed results include point-marker positions, excluded placeholder chapters, clip backups, and project-cleanup backups. Menu option 3 opens the latest text report from the configured work directory.

Final JSON reports add `outcome`, `error_count`, `warning_count`, `marker_count`, `placeholder_count`, start/finish times, and elapsed seconds. Each clip result includes a severity and unresolved results include an action. Warning counts count reported events (a retained project may also contain a missing export); per-category counts and explanations identify the actual affected items. Placeholder totals count baseline chapters excluded from outputs planned or written in this run, not chapters permanently erased from state.

| Status | Meaning |
| --- | --- |
| `ready` | Preview found a verified clip that needs a write. |
| `updated` | Output and audio/video preservation were verified. |
| `up_to_date` | Desired chapters already exist; no rewrite. |
| `no_markers` | Segment has no annotations; no media write. |
| `unmatched` | No saved segment matches this video. This differs from having zero markers. |
| `missing_export`, `ambiguous`, `invalid_project`, `error` | Inspect the report. Affected media was not intentionally replaced unless the error occurred after the final commit; a pending transaction identifies that case. |

Exit codes: **0** = no unresolved items; **2** = completed with items needing attention; **1** = setup/configuration/run failure; **130** = user interruption. Empty batches report zero projects/clips and do no media work.

Unused point markers now count as items needing attention even when project deletion is disabled. A completed batch can contain successful updates alongside warnings or item-level errors; its report distinguishes those from a failure to start the run.

Cleanup has its own `cleanup_results` and `cleanup_summary` in JSON and a separate section in text reports:

| Cleanup status | Meaning |
| --- | --- |
| `would_delete` | Preview candidate; Apply must finish all writes and checks before deletion. |
| `deleted` | All checks passed; project removed and verified backup retained. |
| `retained` | Project remains because segments, markers, or processing results are incomplete. The reason is included. |
| `error` | Cleanup verification, backup, or deletion failed; inspect the reason and any backup path. |
| `prepared` | Interrupted cleanup: the verified backup was recorded before deletion. Check whether the original project still exists. |

Retained projects and cleanup errors count as unresolved items (exit code 2). An interruption after deletion but before the final report update can leave a `prepared` entry; its backup path and project checksum allow recovery. Restore that verified backup if the project is missing and you want it back. An `error` during backup creation may leave an incomplete backup, so verify its checksum before using it.

Backups remain under `work_dir/backups/<run-id>/`, preserving paths relative to the destination root. Reports/state identify each backup. To undo an update, restore the recorded backup to its destination while no application is writing it. Do not restore an older backup over subsequent editing work without reviewing it.

A `run.lock` prevents simultaneous runs using the same work directory. After an abnormal exit, remove the lock only after confirming its recorded process is no longer running.

If a `state/*.pending.json` exists, the tool stops processing that destination. It records the destination, backup, chapter data, and (once prepared) output checksum. A crash can occur before or after atomic replacement. Compare the destination to that checksum before finalizing state or restoring a backup; retain the pending file until recovery is resolved. This first version has no automatic recovery command.

## Scope and limits

- Separate, unretimed exports corresponding to one saved segment are supported. Merged, re-encoded, or smart-cut clips may fail packet verification and are skipped when timing cannot be proven.
- Originals must remain available and uniquely identifiable. Source containers with nonzero start timestamps beyond 50 ms are rejected because their LosslessCut time origin has not been verified.
- Duration/start-offset discrepancies above eight seconds are rejected. This may reject valid exports with unusually long keyframe spacing.
- Five unique packet matches are required, so very short or unusual clips may need manual handling.
- Names and positions transfer; arbitrary marker colors and separate notes do not. Same-millisecond labels are combined.
- Content streams are copied, including subtitles/attachments/data where the container supports them. QuickTime chapter data tracks are regenerated. Unsupported streams produce errors instead of intentional silent omission.
- Chapter updates still read/write the entire marked clip. Verification adds reads. Processing is sequential to avoid saturating the media drive.
- Original recordings are read only. `.llc` contents are never edited; opt-in project cleanup can back up and delete completed projects. Category folders are not rearranged.

## Development and tests

Tests use Python's built-in `unittest`. Integration tests generate small synthetic H.264/AAC clips, so the test FFmpeg build needs `libx264`, AAC, and lavfi `testsrc2`/`sine`. Runtime use does not require an encoder.

```powershell
# Keep generated test media off the system drive.
$env:TEMP = "G:\GPT\Temp"
$env:TMP = "G:\GPT\Temp"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Coverage includes JSON5 parsing, marker boundaries, filename matching, ambiguous associations, timestamp alignment, MP4 chapter layout, metadata escaping, preview preservation, backups, updates/removals, repeat runs, output protection, and project cleanup. Cleanup tests cover zero-marker exports, per-project eligibility, partial failures, changed files, exact backups, restoration, copy mode, and interrupted deletion. Additional tests cover placeholder cleanup/restoration, preserving explicitly named point markers, attention reporting, and real Windows setup resets in disposable fixtures that retain configuration, media, and recovery data.
