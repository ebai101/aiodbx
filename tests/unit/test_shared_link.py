from __future__ import annotations

from typing import Any, cast

import pytest

from aiodbx import SharedLink


def test_shared_link_validation() -> None:
    with pytest.raises(ValueError):
        SharedLink("")
    with pytest.raises(TypeError):
        SharedLink(cast(Any, 1))
    with pytest.raises(TypeError):
        SharedLink("url", password=cast(Any, 1))
    assert SharedLink("url", password="").password == ""
    assert "secret" not in repr(SharedLink("url", password="secret"))
