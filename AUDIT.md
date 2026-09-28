# September 28, 2026 code audit

Checkpoint before iteration: `c4ffa52` — **Fix timing verification for repetitive footage and clarify cut diagnostics**. The checkpoint was committed before the menu or audit changes. No commits were pushed.

## Changes and reproduced failures

| Area | Finding | Resolution |
| --- | --- | --- |
| Menu | `choice` launched a job immediately after a digit. | A line-based PowerShell prompt requires Enter, validates the whole entry, and exits safely on closed input. Dedicated return codes prevent a missing/broken helper from accidentally launching Preview. |
| Competing matches | A clip participating in one ambiguous match could still be modified through another project's unique match. | Reserve every candidate's claim, including ambiguous candidates. Conflicts remain untouched. |
| Original recordings | A recording beside its project could also match another segment's export filename and be rewritten. | Protect discovered source recordings and adjacent project recordings before processing jobs. |
| Copy destination race | A new destination created while remuxing could be accepted and overwritten during installation. | Carry the originally observed destination state through installation and recheck it before backup and replacement. |
| Same-size/timestamp changes | Copy outputs, inputs, and project content could change while retaining their file attributes. | Check content hashes; edited owned outputs are rejected, changed copy inputs regenerate their copies, and project/input changes during processing cancel replacement. |
| Damaged state | A JSON array/null or malformed chapter list could crash the batch. | Validate completed-state structure and report a per-clip error so unrelated clips continue. |
| Directory scans | Inaccessible directories could silently appear empty; Windows junctions were not covered by the symlink check. | Surface scan failures, skip junctions, and reject linked destinations. |
| Folder configuration | Copied outputs could be placed inside the original source tree. | Reject that overlap and storage roots that contain configured scan roots. Validate text settings. |
| Invalid timing | NaN/infinite packet timestamps could bypass numeric comparisons; enormous project timestamps could raise an uncaught overflow. | Reject invalid timestamps and report oversized project values as project errors. |

The first five adversarial integration tests failed against the checkpoint, demonstrating actual defects before their fixes. Additional tests reproduced same-signature input/project changes, ignored scan errors, non-finite packet acceptance, and unsafe output configuration.

## Verification

- **76 tests passed**, including all existing tests and the new adversarial cases. No optional tests were skipped on this machine.
- Actual Windows launcher tests send `1` or `2` without a newline and verify that no job starts, then send Enter and verify the chosen mode. Additional tests cover blank/invalid/shell-like text, closed input, missing prompt helper, and a repository path containing spaces, `&`, and `!`.
- Synthetic AV1 video with two FLAC audio tracks passed marker writing, encoded-stream hash verification, and repeat-run checks. Existing H.264/AAC tests also passed.
- Simulated final-state write failure after media replacement leaves a checksum-addressed pending transaction and verified original backup, retains the project, and blocks unsafe retries.
- Windows junction tests confirm that the linked content is not scanned or selected for writing and that the target remains untouched.
- A read-only preview using copies of the current/backed-up projects verified the existing **47 clips and 30 markers**: 23 outputs up to date, 24 without new markers, no missing exports, unmatched clips, unused markers, or errors. This preview used the existing processing records and did not modify footage or delete projects.
- Whitespace checks passed. All generated media/tests used G: temporary storage. Local settings, real footage, source recordings, and existing backups were preserved.

Preview report: `.llc-markers-work/reports/20260928T213737Z-2c7426.txt` (and JSON). Detailed local test output is under `G:\GPT\Work\losslesscut-embed-markers\Run Review 20260928\code-audit-tests.txt`.

## Remaining improvements and limits

1. **Completion tracking remains deferred.** Deleting a successfully processed project still removes its filename association on subsequent scans. This audit did not add completion receipts, adopt historical batches, or migrate state after media moves.
2. **Guided recovery would improve usability.** Pending transactions remain protected, but resolving them requires inspecting the recorded checksum/backup manually. A future recovery command should offer verified finish/restore choices without guessing.
3. **Source continuity is sampled.** Beginning/end packet matching verifies offsets for the supported single, unretimed export workflow; it is not an exhaustive comparison of the clip's interior to the source. A deliberately spliced interior with unchanged ends could evade those samples. An optional full packet-sequence check or additional samples near each marker would improve assurance for unfamiliar exports.
4. **Concurrency is scoped to one work directory.** Different work directories do not share a lock. The added content checks narrow overwrite races, but they do not provide an operating-system transaction spanning all files. Keep a single work directory for a batch and finish exporting before Apply.
5. **Extra checks require extra reads.** Copy-mode input/output checks and Apply's pre-replacement content checks add disk I/O. Legacy copy-mode records without an input checksum regenerate their owned output once; preview makes that visible. Existing in-place records remain compatible.

These limits are not evidence of damage to the verified batch. Automated tests and simulated interruptions do not establish protection against every hardware failure or concurrent editing pattern.
