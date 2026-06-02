"""Clear licensing.api_key in a settings.yml for distribution.

Usage: python scripts/clear_api_key.py dist/data/settings.yml
"""
import sys

path = sys.argv[1]

with open(path, encoding="utf-8") as f:
    lines = f.readlines()

in_licensing = False
for i, line in enumerate(lines):
    if line.startswith("licensing:"):
        in_licensing = True
    elif in_licensing and line and not line[0].isspace():
        in_licensing = False
    if in_licensing and line.lstrip().startswith("api_key:"):
        indent = len(line) - len(line.lstrip())
        lines[i] = " " * indent + 'api_key: ""\n'
        break

with open(path, "w", encoding="utf-8") as f:
    f.writelines(lines)
