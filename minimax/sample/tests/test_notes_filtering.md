# Acceptance tests: note filtering

- `GET /api/topics/{scope_id}?kind=conclusion` returns only conclusion notes.
- `GET /api/topics/{scope_id}` continues returning every note in existing order.
- An unknown kind returns an empty notes list without modifying stored notes.
