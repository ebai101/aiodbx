from __future__ import annotations

import os
import re
import uuid


def test_run_root() -> str:
    raw = os.environ.get("GITHUB_RUN_ID") or uuid.uuid4().hex
    run_id = re.sub(r"[^a-zA-Z0-9_-]", "-", raw)
    return f"/aiodbx-integration/{run_id}"
