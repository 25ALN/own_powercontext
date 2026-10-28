# Note filtering specification

The topic detail API must support filtering notes by `kind`.
When `kind` is omitted, all notes remain visible in their existing order.
An unknown kind returns an empty list and must not change stored notes.
