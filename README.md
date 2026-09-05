# aiodbx

An asyncio-native Dropbox API v2 client for Python. `aiodbx` follows the naming pattern of the official Dropbox Python SDK, while providing an awaitable API with streaming file downloads.

This library is in very early development. The current release implements a small number of file API operations.

## Requirements

- Python 3.11+
- A Dropbox API access token

An app token with narrow permissions and scoped access to an app folder is recommended while evaluating the library.

## Quick start

Set an access token in your environment:

```bash
export AIODBX_ACCESS_TOKEN='your-access-token'
```

Upload bytes, download the file, and verify that Dropbox reports the expected
content hash:

```python
from __future__ import annotations

import asyncio
import hashlib
import os

from anyio import Path

from aiodbx import AsyncDropbox


def dropbox_content_hash(data: bytes) -> str:
    """Calculate Dropbox's content hash for an in-memory payload."""
    block_size = 4 * 1024 * 1024
    overall = hashlib.sha256()

    for offset in range(0, len(data), block_size):
        block = data[offset : offset + block_size]
        overall.update(hashlib.sha256(block).digest())

    return overall.hexdigest()


async def main() -> None:
    token = os.environ["AIODBX_ACCESS_TOKEN"]
    remote_path = "/aiodbx-example.txt"
    local_path = Path("aiodbx-example.txt")
    content = b"Hello from aiodbx!\n"

    async with AsyncDropbox(token) as dbx:
        uploaded = await dbx.files_upload(
            content,
            remote_path,
            mode="overwrite",
            content_hash=dropbox_content_hash(content),
        )
        print(f"Uploaded {uploaded['path_display']}")

        downloaded = await dbx.files_download_to_path(
            remote_path,
            local_path,
            overwrite=True,
        )
        print(f"Downloaded {downloaded['path_display']}")

    assert await local_path.read_bytes() == content
    print("Upload and download verified.")


asyncio.run(main())
```

## Download a file

`files_download()` is a direct wrapper for Dropbox's `/2/files/download`
endpoint. It returns an async context manager so the HTTP response is always
closed correctly.

```python
from __future__ import annotations

import asyncio
import os

from aiodbx import AsyncDropbox


async def main() -> None:
    async with AsyncDropbox(os.environ["AIODBX_ACCESS_TOKEN"]) as dbx:
        async with dbx.files_download("/reports/latest.csv") as download:
            print(download.metadata["name"])

            async for chunk in download.iter_bytes(chunk_size=1024 * 1024):
                # Write the chunk to an async destination, hash it, or process it.
                print(f"received {len(chunk)} bytes")


asyncio.run(main())
```

For a safe local-file convenience, prefer:

```python
await dbx.files_download_to_path(
    "/reports/latest.csv",
    "latest.csv",
)
```

## Implemented endpoints

| Method | Dropbox endpoint |
|---|---|
| `users_get_current_account()` | `/2/users/get_current_account` |
| `files_get_metadata()` | `/2/files/get_metadata` |
| `files_list_folder()` | `/2/files/list_folder` |
| `files_list_folder_continue()` | `/2/files/list_folder/continue` |
| `files_download()` | `/2/files/download` |
| `files_upload()` | `/2/files/upload` |
| `files_create_folder_v2()` | `/2/files/create_folder_v2` |
| `files_delete_v2()` | `/2/files/delete_v2` |

## Helper functions

| Method | Behavior |
|---|---|
| `files_list_folder_iter()` | Iterates through all pages from `files_list_folder()` |
| `files_download_to_path()` | Streams a Dropbox file to a local path via AnyIO and atomically replaces the destination after success |

