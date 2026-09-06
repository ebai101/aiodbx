# aiodbx

An asyncio-native Dropbox API v2 client for Python. `aiodbx` follows the naming pattern of the official Dropbox Python SDK, while providing an awaitable API with streaming file downloads.

This library is in very early development. The current release implements a small number of file API operations, plus a generic RPC method for calling endpoints that are not implemented yet.

## Requirements

- Python 3.11+
- A Dropbox API access token

An app token with narrow permissions and scoped access to an app folder is recommended while evaluating the library.

## Quick start

Set an access token in your environment:

```bash
export AIODBX_ACCESS_TOKEN='your-access-token'
```

Upload bytes, download the file, and verify that Dropbox reports the expected content hash:

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

## Downloading files

`files_download()` is a direct wrapper for Dropbox's `/2/files/download` endpoint. It returns an async context manager so the HTTP response is always closed correctly.

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

In most use cases, you'll want to save to a file. This will download the file to a temp path and atomically replace the destination on success:

```python
await dbx.files_download_to_path(
    "/reports/latest.csv",
    "latest.csv",
)
```

## Upload sessions

Use upload sessions when data must be sent in multiple blocks, such as when building a streaming or resumable upload workflow. These are direct wrappers around Dropbox's upload-session endpoints:

- `files_upload_session_start`
- `files_upload_session_append_v2`
- `files_upload_session_finish`

Each call accepts one in-memory bytes-like block (`bytes`, `bytearray`, or `memoryview`). `aiodbx` does not automatically retry a block upload after an ambiguous transport or server failure: Dropbox may have received the block and advanced the remote session offset. If an application needs recovery behavior, it should persist the session ID and the last **confirmed** offset.

This example uploads three blocks. `files_upload_session_finish()` sends the final block and commits the completed session to a Dropbox path, so do not append the final block separately.

```python
async with AsyncDropbox(token) as dbx:
    first = b"first block\n"
    middle = b"middle block\n"
    last = b"last block\n"

    started = await dbx.files_upload_session_start(first)
    cursor = {
        "session_id": started["session_id"],
        "offset": len(first),
    }

    await dbx.files_upload_session_append_v2(cursor, middle)
    cursor["offset"] += len(middle)

    metadata = await dbx.files_upload_session_finish(
        cursor,
        {
            "path": "/upload-session-example.txt",
            "mode": "overwrite",
            "autorename": False,
            "mute": False,
            "strict_conflict": False,
        },
        last,
    )

print(f"Uploaded {metadata['path_display']} ({metadata['size']} bytes)")
```

For a one-block upload session, call `files_upload_session_start()` with an empty body, then call `files_upload_session_finish()` with the complete payload. In normal application code, prefer `files_upload()` for small in-memory content. A future `files_upload_path()` helper will stream local files in bounded chunks rather than loading the full file into memory.

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
| `files_upload_session_start()` | `/2/files/upload_session/start` |
| `files_upload_session_append_v2()` | `/2/files/upload_session/append_v2` |
| `files_upload_session_finish()` | `/2/files/upload_session/finish` |
| `files_upload_session_finish_batch()` | `/2/files/upload_session/finish_batch` |
| `files_upload_session_finish_batch_check()` | `/2/files/upload_session/finish_batch/check` |


## Helper functions

| Method | Behavior |
|---|---|
| `files_list_folder_iter()` | Iterates through all pages from `files_list_folder()` |
| `files_download_to_path()` | Streams a Dropbox file to a local path | 


## Requesting unimplemented endpoints

`AsyncDropbox.rpc()` provides access to Dropbox JSON-RPC endpoints that do not yet have a first-class `aiodbx` method:

```python
result = await dbx.rpc(
    "/2/users/get_space_usage",
    {},
)
```

`rpc()` sends a JSON-object payload to `api.dropboxapi.com` and returns a raw JSON-object response. It uses the same token, timeouts, retry policy, and exception mapping as named client methods.

It does not support Dropbox content-upload, content-download, or long-poll endpoints. Those endpoint types use different wire formats and should be accessed through dedicated methods.
