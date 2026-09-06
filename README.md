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


async def main():
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


async def main():
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

## Uploading files

Use `files_upload_path()` to upload one local file. It uses Dropbox's simple upload endpoint for files within Dropbox's simple-upload limit and uses a managed upload session for larger files.

The helper reads large sources in bounded chunks. It owns the upload session only for the duration of the call; it does not persist resumable upload state or retry a body-bearing request after an ambiguous transport or server failure.

```python
from __future__ import annotations

import asyncio
import os

from anyio import Path

from aiodbx import AsyncDropbox


async def main():
    source = Path("dist/release.tar.zst")

    async with AsyncDropbox(os.environ["AIODBX_ACCESS_TOKEN"]) as dbx:
        metadata = await dbx.files_upload_path(
            source,
            "/releases/release.tar.zst",
            mode="overwrite",
        )

    print(f"Uploaded {metadata['path_display']} ({metadata['size']} bytes)")


asyncio.run(main())
```

Use `files_upload()` when the content is already available in-memory:

```python
metadata = await dbx.files_upload(
    b"generated report\n",
    "/reports/latest.txt",
    mode="overwrite",
)
```

## Batch-uploading local files

Use `files_upload_paths()` to stream and batch-commit several local files. Pass each source/destination pair as an `UploadPath`.

```python
from __future__ import annotations

import asyncio
import os

from anyio import Path

from aiodbx import AsyncDropbox, UploadPath


async def main():
    uploads = [
        UploadPath(
            Path("dist/client-one.tar.zst"),
            "/releases/client-one.tar.zst",
        ),
        UploadPath(
            Path("dist/client-two.tar.zst"),
            "/releases/client-two.tar.zst",
        ),
    ]

    async with AsyncDropbox(os.environ["AIODBX_ACCESS_TOKEN"]) as dbx:
        result = await dbx.files_upload_paths(
            uploads,
            mode="overwrite",
            chunk_size=4 * 1024 * 1024,
        )

    assert result[".tag"] == "complete"

    for entry in result["entries"]:
        if entry[".tag"] == "success":
            metadata = entry["success"]
            print(f"Uploaded {metadata['path_display']}")
        else:
            print(f"Batch entry failed: {entry}")


asyncio.run(main())
```

Every `files_upload_paths()` member uses a Dropbox upload session, including small and empty files. The helper streams sources sequentially in bounded chunks, closes every session before finalization, then commits the sessions through Dropbox's batch session API.

The helper returns the result of the `/upload_session/finish_batch` request. This request may contain failed entries even if it's successful; be sure to inspect the contents of  `result["entries"]`.

Like `files_upload_path()`, this helper does not persist session IDs and does not retry body-bearing upload-session requests after an ambiguous failure. It does retry safe status polling for Dropbox batch jobs according to the client retry policy.

## Advanced upload sessions

Use the low-level `files_upload_session` methods for more granular control over the upload session lifetime:

- `files_upload_session_start()`
- `files_upload_session_append_v2()`
- `files_upload_session_finish()`
- `files_upload_session_finish_batch()`
- `files_upload_session_finish_batch_check()`

Each body-bearing session call accepts one in-memory bytes-like block. `aiodbx` does not automatically retry a start, append, or finish request after a timeout, connection failure, or transient server response because Dropbox may have received the block and advanced the remote session state.

```python
first = b"first block"
middle = b"middle block"
last = b"final block"

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
        "path": "/uploads/example.bin",
        "mode": "overwrite",
        "autorename": False,
        "mute": False,
        "strict_conflict": False,
    },
    last,
)
```


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

## Implementation table

### Single endpoint methods

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


### Helpers

These helpers orchestrate the above methods for convenience, and do not correspond directly to single endpoints.
| Method | Behavior |
| --- | --- |
| `files_list_folder_iter` | Iterates through all pages from `files_list_folder` |
| `files_download_to_path` | Streams a Dropbox file to a local path atomically |
| `files_upload_path` | Uploads one local file, using simple upload or a managed session |
| `files_upload_paths` | Streams local files into sessions and batch-commits them |
