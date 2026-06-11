"""Cross-platform ``file://`` URL → filesystem path conversion.

LibreOffice's ``PackageInformationProvider.getPackageLocation`` returns
the installed extension's location as a ``file://`` URL. Turning that
back into a path with ``Path(urlparse(url).path)`` is correct on POSIX
but WRONG on Windows: ``urlparse("file:///C:/Users/x").path`` is
``"/C:/Users/x"`` and ``Path("/C:/Users/x")`` is not a usable Windows
path — ``is_file()`` / ``is_dir()`` return ``False``, so every
extension-resource lookup (web bundle, icon, pythonpath, LICENSE) fails
and the chat window never opens.

:func:`urllib.request.url2pathname` is the OS-appropriate converter: on
POSIX it percent-decodes the path; on Windows it dispatches to
``nturl2path`` which handles the leading-slash drive form. We route all
``file://`` conversions through here so the platform handling lives in
one place.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname


def file_url_to_path(url: str) -> Path:
    """Convert a ``file://`` URL to a filesystem :class:`~pathlib.Path`.

    Args:
        url: A ``file://`` URL (e.g. from ``getPackageLocation``).

    Returns:
        The corresponding filesystem path, correct on POSIX and Windows.

    Raises:
        ValueError: If ``url`` is not a ``file://`` URL — callers depend
            on extension resources living on the local filesystem, so a
            non-file scheme is a hard error rather than a silent miss.
    """
    parsed = urlparse(url)
    if parsed.scheme != "file":
        raise ValueError(f"expected a file:// URL, got {url!r}")
    return Path(url2pathname(parsed.path))
