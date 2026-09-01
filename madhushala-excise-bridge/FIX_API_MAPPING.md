# Mapping API fix

Changes applied:

- Madhushala bearer tokens are normalized before use. A value pasted as `Bearer <token>` is reduced to the raw token so the client never sends `Bearer Bearer ...`.
- `/api/purchase/dropdown/items` now receives the same `Authorization: Bearer <token>` header as the other Madhushala mapping endpoints.
- Frontend API parsing now preserves non-JSON upstream error bodies instead of replacing them with a generic request error.

The bridge token endpoint remains `POST /madhushala/token`; the token is a Madhushala Report API bearer token, not an extension API key.
