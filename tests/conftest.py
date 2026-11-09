import os
import sys

# Ensure the project root is on sys.path and sys.argv[0] points there
# so config.py resolves data_path correctly.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.argv[0] = os.path.join(PROJECT_ROOT, "main.py")
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Log files go to a throwaway directory: importing api.py calls setup_logger(),
# which would otherwise write test fixtures' errors into the real logs/ when
# data/settings.yml has debug.logging_in_file: true (ADR 0010).
import tempfile  # noqa: E402

os.environ["GREX_LOG_DIR"] = tempfile.mkdtemp(prefix="grex-test-logs-")
