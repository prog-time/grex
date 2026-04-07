# API Grex Parser — Project Rules

> **Context:** Read this file before creating, modifying, or extending any part of the `api_grex_parser` FastAPI project. This project is a standalone API key management and access-control service.
> **Version:** 1.1

---

## 1. Purpose

`api_grex_parser` is a standalone FastAPI service that:

- Issues and manages per-client API keys
- Stores which **sources** each client is allowed to access
- Exposes an HTTP endpoint to validate a key and retrieve its allowed sources
- Provides an admin panel (starlette-admin) for the operator to manage clients and subscriptions

This service has no knowledge of what "sources" mean — it only stores and returns their names as strings. The caller decides how to interpret them.

---

## 2. Stack

| Component | Choice |
|---|---|
| Framework | FastAPI |
| ORM | SQLAlchemy 2 async |
| DB driver (dev) | aiosqlite |
| DB driver (prod) | asyncpg (PostgreSQL) |
| Admin panel | starlette-admin |
| Config | PyYAML via `data/settings.yml` |
| Logging | Loguru |
| Server | uvicorn |
| Migrations | Alembic |
| JWT signing | PyJWT + cryptography (RS256) |

Never introduce a framework or library not listed here without updating this file first.

---

## 3. Directory Structure

```
api_grex_parser/
├── admin/
│   ├── __init__.py          ← setup_admin(), teardown_admin()
│   └── resources/
│       └── api_keys.py      ← ApiKeyView (starlette-admin resource)
├── models/
│   ├── __init__.py
│   ├── db.py                ← Base, async_session, init_db()
│   └── api_key.py           ← ApiKey ORM model
├── services/
│   ├── __init__.py
│   └── api_key_service.py   ← lookup, validate, create, update
├── migrations/              ← Alembic migration files
├── data/
│   └── settings.yml
├── tests/
├── api.py                   ← FastAPI app, routes, lifespan
├── config.py                ← Config loader
└── requirements.txt
```

Never put business logic in `api.py`. Every new domain concern must have its own file under `models/` and `services/`.

---

## 4. Data Model — `ApiKey`

```python
# ✅ Correct
class ApiKey(Base):
    __tablename__ = "api_keys"

    id              = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    key_hash        = Column(String(64), unique=True, nullable=False, index=True)
    owner_name      = Column(String(255), nullable=False)
    allowed_sources = Column(Text, nullable=False, default='["*"]')  # JSON array of strings
    is_active       = Column(Boolean, nullable=False, default=True)
    created_at      = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    expires_at      = Column(DateTime, nullable=True)
    comment         = Column(Text, nullable=True)
```

```python
# ❌ Incorrect — raw key stored in DB
class ApiKey(Base):
    key = Column(String(64), nullable=False)
```

### 4.1 Field rules

- `key_hash` — SHA-256 hex digest of the raw key. The raw key is shown **once** at creation and never stored.
- `allowed_sources` — JSON array of arbitrary source name strings. Use `["*"]` to grant access to all sources.
- `is_active` — must be checked before `expires_at`. A deactivated key is always denied regardless of expiry.
- `expires_at` — `null` means the key never expires. Always store and compare in UTC.

---

## 5. API Endpoints

### 5.1 Endpoint inventory

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/health` | No | Liveness check |
| `POST` | `/config` | Client key | **Called by parser on startup.** Returns signed JWT tokens for allowed sources |
| `POST` | `/keys/validate` | Master key | Validate a client key, return allowed sources |
| `POST` | `/keys` | Master key | Create a new API key |
| `GET` | `/keys` | Master key | List all keys (paginated) |
| `GET` | `/keys/{id}` | Master key | Get a single key by ID |
| `PATCH` | `/keys/{id}` | Master key | Update key fields |
| `DELETE` | `/keys/{id}` | Master key | Delete a key |

The **master key** is the operator's own key stored in `settings.yml`. It is never stored in the DB.

### 5.2 `POST /config` — Fetch license config (called by parser on startup)

This is the **primary integration endpoint**. The parser calls it once on application startup using its client key (`X-API-Key` header).

**Authentication:** `X-API-Key` header with the client's raw API key.

**Request:** no body required.

**Response `200`:**
```json
{
  "client_id": "550e8400-e29b-41d4-a716-446655440000",
  "issued_at": "2026-03-30T10:00:00Z",
  "tokens": [
    "eyJhbGciOiJSUzI1NiJ9...signed_token_for_source_a",
    "eyJhbGciOiJSUzI1NiJ9...signed_token_for_source_b"
  ]
}
```

**JWT token payload (each token in `tokens`):**
```json
{
  "source": "source_a",
  "client_id": "550e8400-e29b-41d4-a716-446655440000",
  "iat": 1743328800,
  "exp": 1745920800
}
```

Rules:
- Sign every token with RS256 using the server's **private key**.
- The parser decodes the JWT payload (base64) to read the `source` claim — no signature verification on the client side.
- Each token contains exactly one `source` claim — one token per allowed source.
- `client_id` in every token **must match** the `client_id` field in the response root.
- `exp` must equal the `ApiKey.expires_at` value, or a reasonable default (e.g. 30 days) when `expires_at` is null.
- Return `401` when the key is missing, `403` when it is invalid, inactive, or expired.
- Never include source names anywhere in the response outside of the JWT payload — the tokens are the only source of truth.

```python
# ✅ Correct — token generation
import jwt
from datetime import datetime, timezone, timedelta

def generate_source_token(source: str, client_id: str, private_key, expires_at: datetime) -> str:
    payload = {
        "source": source,
        "client_id": client_id,
        "iat": datetime.now(timezone.utc),
        "exp": expires_at,
    }
    return jwt.encode(payload, private_key, algorithm="RS256")
```

```python
# ❌ Incorrect — returning plain source names instead of tokens
return {"sources": ["source_a", "source_b"]}
```

**RSA key pair management:**
- Generate once: `openssl genrsa -out private_key.pem 2048 && openssl rsa -in private_key.pem -pubout -out public_key.pem`
- Store `private_key.pem` in `data/` on the `api_grex_parser` server — never expose it.
- The public key is **not** distributed to parsers — parsers read `source` from the JWT payload without signature verification.

---

### 5.3 `POST /keys/validate`

**Request:**
```json
{ "key": "<raw client key>" }
```

**Response `200` — valid:**
```json
{
  "valid": true,
  "owner_name": "Client Name",
  "allowed_sources": ["source_a", "source_b"]
}
```

**Response `200` — invalid:**
```json
{
  "valid": false,
  "reason": "key_not_found" | "inactive" | "expired"
}
```

Rules:
- Always return HTTP `200` for both valid and invalid keys — never `404` for unknown keys.
- Hash the incoming key with SHA-256 and look up by `key_hash`. Never iterate all records.
- Validate in order: existence → `is_active` → `expires_at`.

```python
# ✅ Correct
import hashlib

def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()
```

### 5.4 `POST /keys` — Create key

- Generate a raw key: `secrets.token_urlsafe(32)`
- Store only `key_hash`
- Return the raw key **once** in the response — it is never retrievable again
- Validate that `allowed_sources` is a non-empty list of strings or `["*"]`

**Response `201`:**
```json
{
  "id": "uuid",
  "key": "<raw key — shown once>",
  "owner_name": "Client Name",
  "allowed_sources": ["source_a"],
  "expires_at": null
}
```

### 5.5 `PATCH /keys/{id}` — Update key

Updatable fields: `allowed_sources`, `is_active`, `expires_at`, `comment`, `owner_name`.

Forbidden: `key_hash`, `id`, `created_at`. Reject any request attempting to update these.

### 5.6 `GET /health`

**Response `200`:**
```json
{ "status": "healthy" }
```

---

## 6. Admin Panel

Use `starlette-admin` with `SqlAlchemyModelView` for `ApiKey`.

### 6.1 Rules

- Display `key_hash` as read-only, truncated to the first 8 characters + `…` — never the full value.
- Provide `allowed_sources` as a multi-line text field (comma-separated) or tag input.
- Add a custom "Generate new key" action that calls the create logic and displays the raw key in a one-time modal.
- Display `expires_at` with a date picker. Empty = never expires.
- The admin panel is protected by session-based login (starlette-admin built-in auth), credentials from `settings.yml`.

### 6.2 Forbidden

- ❌ Never allow editing `key_hash` via the admin panel.
- ❌ Never display the full `key_hash`.
- ❌ Never allow bulk-delete without a confirmation step.

---

## 7. Authentication Rules

### 7.1 Master key

- Stored in `settings.yml` under `security.master_key`.
- Required as `X-Master-Key` header on all `/keys/*` endpoints.
- Implemented as a FastAPI dependency `verify_master_key()`.
- Never stored in the DB.

```python
# ✅ Correct
async def verify_master_key(x_master_key: str = Header(...)) -> None:
    if not secrets.compare_digest(x_master_key, settings["security"]["master_key"]):
        raise HTTPException(status_code=403, detail="Forbidden")

# ❌ Incorrect — timing-unsafe comparison
if x_master_key == settings["security"]["master_key"]:
    ...
```

### 7.2 Keys in transit

- Never accept a client key as a query parameter — only in the request body.
- Never log a raw key — log only the first 8 characters + `…`.

```python
# ✅ Correct
logger.info(f"Key validated: {raw_key[:8]}…")

# ❌ Incorrect
logger.info(f"Key validated: {raw_key}")
```

---

## 8. Configuration (`data/settings.yml`)

```yaml
app:
  host: localhost
  port: 8010

security:
  master_key: "change-this-to-a-random-32-char-string"

admin:
  username: admin
  password: change-me
  secret_key: change-me-in-production

database:
  url: "sqlite+aiosqlite:///data/grex_parser_api.db"
  # Production: "postgresql+asyncpg://user:pass@localhost/grex_parser_api"

debug:
  active: false
```

Never commit `settings.yml` with real credentials. Always provide `settings.yml.example` with placeholder values.

Warn on startup if `master_key` is shorter than 32 characters or equals the placeholder value.

---

## 9. Database Migration Rules

- All schema changes must go through Alembic migrations — never `Base.metadata.create_all()` in production.
- `create_all()` is allowed only in tests and local development.
- Every migration must implement `downgrade()`.
- Migration files must contain schema changes only — no business logic.

---

## 10. Security Rules

- Store only `key_hash` (SHA-256). A leaked DB must not expose raw client keys.
- Use `secrets.compare_digest()` for all string comparisons involving keys or tokens.
- Rate-limit `POST /keys/validate` to prevent brute-force enumeration: max 100 req/min per IP.
- All datetimes must be UTC (`datetime.now(timezone.utc)`). Never use naive datetimes.
- The admin panel must use HTTPS in production.

---

## 11. Checklist

- [ ] `ApiKey` model matches Section 4 — no raw key stored
- [ ] `POST /config` returns JWT tokens signed with RS256 private key
- [ ] Each JWT token contains `source`, `client_id`, `iat`, `exp`
- [ ] `client_id` in every token matches `client_id` in response root
- [ ] Source names are never returned outside of JWT payloads
- [ ] RSA key pair generated, `private_key.pem` stored on server only
- [ ] `POST /keys/validate` always returns HTTP `200`
- [ ] Master key auth on all `/keys/*` endpoints via dependency
- [ ] Raw key returned only once on creation, never stored
- [ ] Admin panel does not display full `key_hash`
- [ ] All datetimes in UTC
- [ ] `settings.yml.example` present, real credentials never committed
- [ ] `private_key.pem` in `.gitignore` on the server — never committed
- [ ] Alembic configured, `create_all` only in dev/tests
- [ ] Raw keys never logged
- [ ] `secrets.compare_digest()` used for all key comparisons
