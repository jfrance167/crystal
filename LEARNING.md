# Learning

An API key is a credential, while a fingerprint is an identifier. Audits need identifiers and outcomes, not raw credentials.

Rotation is a sequence of trust transitions: create, test, switch, verify and revoke. Revoking too early can break the consumer; cleanup failure is a distinct outcome even when the consumer works.

Idempotency prevents a retried request from creating another credential. Serialization prevents different concurrent requests from overlapping. Both are necessary and solve different problems.

Loopback binding limits network reach but does not stop a hostile website from attempting browser requests. Exact Host/Origin validation, a session token and no CORS access add relevant protections.

The local worker produced UI suggestions, not verified security judgments. Its earlier suggestion to revoke immediately was rejected because it violated the core safety invariant.
