# Dead Ends: failed approaches log

This file records approaches that failed and must **never** be repeated. Add a row whenever an
approach is abandoned, with enough detail that the next agent won't try it again.

| Date | Approach Tried | Why It Failed | Files Touched |
|------|----------------|---------------|---------------|
| 2026-11-14 | Treating OpenMoonstone's DOS loaders (`piv.py`, `cmp.py`, `lz77.rs`) as the Amiga format reference | Not attempted in code; ruled out during alignment. OpenMoonstone reads the **DOS** release, and the Amiga files on the ADFs use different containers. moonstone-ace is the Amiga format reference. | n/a |
