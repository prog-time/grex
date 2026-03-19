"""Admin credentials are stored in data/settings.yml.

admin:
  username: "admin"
  password: "changeme"
  secret_key: "change-me-in-production"
  db_url: "sqlite:///data/admin.db"

No database model required — starlette-admin uses session-based auth
via SettingsAuthProvider in admin/__init__.py.
"""
