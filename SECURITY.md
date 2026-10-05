# Security scope

Crystal is a synthetic, disposable lab bound to numeric IPv4 loopback. Never supply production credentials or expose it through a reverse proxy, tunnel, port forwarding or public interface.

## Assets and relevant threats

Protect credential transition order, availability of the working demo consumer, absence of raw keys from metadata/output, and integrity of the browser mutation boundary. Address premature revocation, overlapping/retried attempts, interrupted operations, failed cleanup, browser CSRF/DNS-rebinding attempts, and unbounded/untrusted requests.

## Implemented controls

- Cryptographically random synthetic credentials; hashed provider membership, no raw persisted credential.
- Verify both candidate and consumer before old-key revocation. Verification failure restores the working consumer; cleanup failure is actionable and blocks later rotations.
- Serialize attempts, reject overlapping jobs, retain UUID results for retries, and coalesce missed intervals.
- An OS file lock prevents simultaneous services using the same runtime store. Audit metadata persists; restart resets the disposable credential session.
- Bind only to `127.0.0.1`. Validate exact numeric Host and exact browser Origin, reject cross-site Fetch Metadata, require a session token for mutations, and refuse CORS preflight/session access without a custom header.
- Fixed routes, bounded JSON bodies, strict field/type validation, parameterized SQLite, text-only DOM rendering, CSP, no embedded third-party resources, and no request logging.
- The CLI disables proxies and redirects. Unexpected rotation errors block further attempts and do not expose exception strings to clients/audit events.

## Boundaries

The session token is obtainable by trusted local software. There is no user identity, TLS, production vault, immutable logging, robust hostile-client denial-of-service defense, external provider integration, or seamless secret recovery. In-process rotation checks model provider calls; HTTP integration tests demonstrate the authentication transition. The local HTTP stack and SQLite history are suitable for this bounded learning scenario only.

## Review and reports

The tests cover successful and failed transitions, real loopback old/new-key authentication, malformed requests, authorization boundaries, scheduling, interruptions and redaction. Bandit/static review is additional evidence, not proof of safety.

Jake should manually review `crystal/server.py` request authorization, `crystal/core.py` transition/cleanup policy and these limits before any future production integration or publication. Merge/deployment is not authorized by completion of local checks.

Keep any report free of real keys, tokens, employer/client information or private runtime exports. No public security-reporting channel is configured because no remote repository has been published.
