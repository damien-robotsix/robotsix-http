import importlib
from importlib.metadata import PackageNotFoundError
from unittest.mock import patch

import robotsix_http


def test_version() -> None:
    assert robotsix_http.__version__ is not None
    assert isinstance(robotsix_http.__version__, str)
    assert len(robotsix_http.__version__) > 0


def test_version_fallback() -> None:
    with patch(
        "importlib.metadata.version",
        side_effect=PackageNotFoundError,
    ):
        importlib.reload(robotsix_http)
        assert robotsix_http.__version__ == "0.0.0.dev0"
