"""Talk2View for LibreOffice Writer.

Importing this package configures the persistent rotating log
file (see ``_logging.py``). Every other module in the package
uses ``logger = logging.getLogger(__name__)`` and inherits from
the ``talk2view_writer`` logger's handlers — so just importing
anything from this package gives you a usable log immediately.

To override the log path or level for diagnostics, set:

    T2V_WRITER_DEBUG=1   # verbose (DEBUG-level)

before launching LibreOffice. Log files live at the path printed
on the first log line + visible in the Settings panel.
"""

from talk2view_writer._logging import log_file_path, setup_logging

# Bootstrap logging at package import time so any subsequent code
# (including imports of submodules with module-level loggers) gets
# the configured handlers without each module having to remember to
# call setup_logging() itself.
setup_logging()

__version__ = "1.0.8"

__all__ = ["__version__", "log_file_path", "setup_logging"]
