from __future__ import annotations


def validate_non_root_path(path: str) -> None:
    if not path:
        raise ValueError(
            "get_metadata requires a non-root Dropbox path or an 'id:' identifier."
        )

    if path == "/":
        raise ValueError(
            "Dropbox root is represented by '' for list operations; "
            "get_metadata does not support root metadata."
        )

    if path.startswith("id:"):
        return

    if not path.startswith("/"):
        raise ValueError(
            "Dropbox paths must start with '/', unless using an 'id:' identifier."
        )

    if path.endswith("/"):
        raise ValueError("Dropbox paths must not end with '/'.")
