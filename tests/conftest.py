import os
import sys

# Ensure the project root is on sys.path and sys.argv[0] points there
# so config.py resolves data_path correctly.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.argv[0] = os.path.join(PROJECT_ROOT, "main.py")
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
