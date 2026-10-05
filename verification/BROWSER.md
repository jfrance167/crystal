# Browser observations — October 5, 2026

Tool: existing Playwright CLI 0.1.22 with a separate test browser. Chrome DevTools MCP was unavailable and was not installed. The application was reached only at http://127.0.0.1:8765.

PASS: reusable `tools/browser-smoke.js` completed through actual UI actions:

- Manual rotation: old-key result Rejected, one active key.
- Interval selection and schedule resume: a real ten-second timer increased the success counter; pause was exercised afterward.
- Failed candidate verification: original fingerprint preserved, one active key, explicit failure message.
- Old-key cleanup failure: Cleanup required, two active keys, Rotate key disabled.
- Desktop 1440×1100 and mobile 390×844 full-page screenshots saved. Mobile document width equaled viewport width (390 pixels); the audit table has its own horizontal scroll area.
- Browser console after the smoke test: zero errors and zero warnings.
- Observed navigation duration in the smoke test: 21 ms. This is one local observation, not a benchmark or performance guarantee.

Afterward the owned demo service was restarted with the final controller/error/audit code. The CLI rotation returned success, consumer_healthy=true, old_key_rejected=true, active_keys=1. Its default five-minute schedule was enabled. The final dashboard reload showed a fresh healthy session.

Final screenshot refresh showed HEALTHY, one active key and RUNNING schedule. Pressing Tab focused the skip link with a visible solid outline. The final console again had zero errors/warnings, and the final service error log was empty.

The semantic snapshot has native named buttons/comboboxes, one h1, h2 section headings, a table caption/headers, and a live result region. CSS includes visible keyboard focus and the skip link. This is focused accessibility verification, not a full assistive-technology or WCAG certification.

A transient connection failure exposed a stale reconnect message; polling now announces reconnection after successful refresh. A stale Playwright element reference during exploratory steps was corrected by retrieving current references. The reusable smoke script uses semantic locators and passed.

Backend unit/integration tests and Bandit results are recorded separately in checks.json/tests.txt/bandit.json. No browser request/session token values were captured in this report.
