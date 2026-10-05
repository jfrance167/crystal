# Decisions

## October 5, 2026 — disposable local demo

Use original Python standard-library code rather than fork the archived cloud-oriented Rotator. No runtime dependency installation is needed. The supplied visual is the direction for cream surfaces, near-black typography, angular logo and spaced labels.

Raw synthetic credentials remain in memory; SQLite stores fingerprints and allowlisted events. Restart creates a fresh demo session and marks unfinished attempts interrupted. This deliberately avoids pretending that a demo implements production vault storage or crash-safe credential recovery.

Verify the candidate and consumer before revocation. Before verification finishes, restore the old consumer on failure. If old-key cleanup fails after verification, keep the working new consumer and block subsequent rotations until the disposable demo restarts. This favors explicit failure evidence over silent orphan accumulation.

Scheduler runs in the foreground service only. Missed intervals coalesce, and successful manual attempts reset the interval. Runtime ownership uses an OS file lock so multiple processes cannot reset the same metadata store.

Browser mutations require exact loopback Host/Origin and a per-process session token. No external scripts, fonts or arbitrary targets. Local machine compromise is outside scope.
