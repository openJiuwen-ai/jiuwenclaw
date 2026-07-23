# DeepResearch Windows Artifact Publication Design

## Objective

Make DeepResearch report, sidecar, and asset publication work on native Windows
without weakening the existing descriptor-based hardening on POSIX systems.

The change is limited to publication, verification, and rollback helpers in
`jiuwenclaw/agentserver/tools/deepresearch_tools.py` plus focused unit tests.
It does not merge or rebase PR 3649, change artifact naming or provenance
contracts, or alter the report contents exposed to RelayClaw.

## Root Cause

The current publisher assumes a POSIX filesystem API:

- it opens directories with `os.O_DIRECTORY | os.O_NOFOLLOW`;
- it passes `dir_fd`, `src_dir_fd`, and `dst_dir_fd`;
- it retains directory descriptors while publishing children;
- its rollback path uses the same descriptor-relative operations;
- it calls `os.fchmod`.

Those operations are unavailable on native Windows. A report with inference,
chart, or styled-HTML assets therefore fails before its visible Markdown or HTML
artifact can be published. Error cleanup can fail for the same reason.

## Selected Architecture

Use two internal publication backends selected by an explicit capability
predicate:

1. **Hardened POSIX backend** — retain the current descriptor-relative
   implementation unchanged.
2. **Windows path backend** — use same-parent temporary names and Windows'
   no-replace `os.rename` behavior, without passing descriptor-relative
   arguments or referring to POSIX-only constants.

The dispatcher is private to `deepresearch_tools.py`. Callers such as
`_write_report_markdown` and `_install_styled_bundle` continue using the
existing helper names and artifact records.

The Windows backend is selected only when `os.name == "nt"`. This avoids
silently reducing the security guarantees on POSIX platforms when one optional
operation happens to be missing.

## File Publication

The POSIX backend keeps the existing temporary-file plus hard-link publication.

The Windows backend:

1. creates a temporary file in the destination directory;
2. writes, flushes, and `fsync`s the complete payload;
3. records the temporary file identity;
4. closes the file;
5. calls `os.rename(temp_path, final_path)`.

On Windows, `os.rename` fails when the destination already exists and does not
replace it. Because the temporary file and destination share a parent, the move
does not cross volumes. Consumers never observe a partially written final file.

The helper returns metadata for the published object so rollback can prove
ownership before deletion.

## Asset Directory Publication

The Windows backend publishes a flat asset directory as one complete namespace
operation:

1. validate that every staged child is a regular file;
2. create a uniquely named sibling staging directory under the final parent;
3. publish each child into that sibling directory with the Windows file
   publisher;
4. rename the completed sibling directory to the final directory name;
5. record the final directory identity as one created artifact.

If the final name already exists, the rename fails without replacing it. The
private sibling directory is removed during local cleanup.

Unlike the POSIX backend, Windows does not retain an open directory descriptor.
This also avoids Windows sharing violations when a directory is renamed or
removed.

## Verification

`_verify_created_directories` continues verifying retained descriptors for
POSIX artifacts. For Windows directory artifacts, it obtains `lstat` metadata
from the public path and compares it with the identity recorded immediately
after publication.

A missing path, a symlink/reparse-point substitution, or an identity change
causes publication to fail closed.

## Rollback

The Windows rollback path mirrors the existing quarantine intent using path
operations:

1. atomically rename the public path to a unique sibling quarantine name;
2. inspect the quarantined object without following symlinks;
3. delete it only when its identity matches the recorded artifact;
4. if identity does not match, attempt a no-replace rename back to the original
   name;
5. if restoration cannot be completed because another writer owns the public
   name, leave the quarantined object intact and log its location.

Files are unlinked and asset directories are removed recursively only after the
identity check. The backend never deletes an object merely because it occupies
the expected pathname.

## Error Handling

- Destination collisions remain `FileExistsError`, allowing the existing
  version-allocation retry loop to select another name.
- Invalid staged assets raise `ValueError` before the final directory is
  published.
- Namespace or identity changes raise `RuntimeError`.
- Best-effort rollback logs failures without hiding the original publication
  error.
- Temporary files and sibling staging directories are cleaned in `finally`
  blocks.

## Tests

Add focused tests to `tests/unit/agentserver/test_deepresearch_stream_tool.py`.
Tests run on the development host while forcing the Windows backend directly;
they must not depend on Windows-only constants being present.

Required cases:

1. Windows file publication writes the complete payload and does not overwrite
   an existing destination.
2. Windows asset publication produces the expected flat directory without any
   `dir_fd` call.
3. A destination-directory collision leaves the existing directory unchanged.
4. Successful rollback removes artifacts whose recorded identity still
   matches.
5. Rollback preserves and restores an object that replaced the created
   artifact.
6. The normal Markdown and styled-HTML publication flows select the Windows
   backend when `os.name == "nt"`.
7. Existing POSIX race-hardening tests remain green.

Native Windows CI or a Windows packaging smoke test remains required before
release because a macOS unit test cannot prove Windows filesystem semantics.

## Alternatives Rejected

### Replace the POSIX implementation with one portable path implementation

This is smaller but unnecessarily gives up descriptor-relative protection
against namespace races on Linux and macOS.

### Disable report assets on Windows

This avoids the crash by dropping inference, chart, and styled-HTML artifacts.
It violates feature parity and would turn a platform bug into silent data loss.

### Implement Win32 handle operations through `ctypes`

Native `CreateFileW` and handle-relative disposition APIs could provide stronger
Windows race guarantees, but they add a large platform-specific surface and
packaging burden. The selected rename-and-quarantine design is scoped to the
current threat model and preserves no-overwrite publication without that
complexity.

