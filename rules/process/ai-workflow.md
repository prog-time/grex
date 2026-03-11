# AI Workflow Rules

> Purpose: Constrain AI behaviour to prevent unsafe or low-quality changes and to make results deterministic across agents.
> Context: Read this file before performing any modification, refactor, migration, or feature implementation.
> Version: 1.0

## 1. Core principle

The agent must behave like a careful mid-level engineer, not an auto-generator.
- Always analyze before coding
- Always design before implementing
- Always test before finishing
- Never generate large changes blindly
- Never modify files you did not read

## 2. Mandatory development lifecycle

**Step 1 — Understand**
- Read `rules/README.md`
- Read relevant domain files (`rules/domain/crawling.md`, `rules/domain/product-parsing.md`, `rules/domain/proxy-management.md`)
- Read related source files before proposing changes
- Identify the existing crawler pattern to follow

**Step 2 — Design**
- Propose the crawler structure or API change first
- List impacted files: crawler file, `__init__.py`, `factory.py`, `models/product.py` (if model changes), domain rules
- Identify risks: cookie expiry handling, async/sync Playwright boundary, SKU extraction edge cases
- Do not write code yet

**Step 3 — Implement**
- Make the smallest possible change
- Follow the existing crawler pattern exactly (init, `is_valid_url`, `parse`, `get_headers`, retry loop)
- Reuse `ProxyManager`, `parse_url()`, `AsyncSession(impersonate="chrome136")`
- Avoid introducing new dependencies unless strictly necessary

**Step 4 — Verify**
- Run `python -m py_compile <file>` to check syntax
- Verify the new crawler can be imported
- Confirm factory registration is correct
- Confirm `ParseRequest` validator includes new domains
- Check for security issues (no hardcoded keys or cookies)

**Step 5 — Document**
- Update `rules/domain/crawling.md` with new business rules (numbered)
- Update `rules/database/schema.md` if new file-based state is introduced
- Update `rules/api/endpoints.md` if new endpoints are added
- Update `rules/domain/product-parsing.md` with per-marketplace extraction reference

The task is not complete until documentation is updated.

## 3. Scope control rules

```python
# ✅ Correct — minimal targeted change: only adds new crawler
class LambdaCrawler(BaseCrawler):
    async def parse(self, url: str) -> ProductCard:
        ...

# ❌ Incorrect — mixes in a refactor of existing crawlers while adding a new one
class LambdaCrawler(BaseCrawler):
    # Also rewrote OzonCrawler patterns and renamed ProxyManager methods
    ...
```

One change per task. Do not refactor existing crawlers while adding a new one.

## 4. Generation size limits

- Prefer small iterations over big outputs
- A new crawler should be implemented as one file at a time
- Never regenerate the entire `crawlers/` directory
- Review each file before moving to the next

Large monolithic generations increase hallucination risk, especially for marketplace-specific API structures.

## 5. Safety rules

```python
# ✅ Correct — uses real API endpoint confirmed in codebase
response = await self.session.post(
    url="https://megamarket.ru/api/mobile/v1/catalogService/productCardMainInfo/get",
    ...
)

# ❌ Incorrect — hallucinated endpoint
response = await self.session.get(
    url="https://megamarket.ru/api/v2/product/details",  # does not exist
    ...
)
```

- Never invent marketplace API endpoints — only use URLs confirmed in existing crawlers or real browser network inspection
- Never invent HTML selectors — only use selectors confirmed in existing crawlers
- Never guess `ProductCard` field names — read `models/product.py` first
- Never assume a marketplace returns prices in a specific currency or unit without verifying in the codebase
- Do not fabricate dependencies or packages

## 6. Test-first expectations

- Add or update tests for every new crawler
- Bug fixes must include a regression test demonstrating the fix
- New crawlers must include: happy path (returns `ProductCard`), missing SKU case, `ProductCardParsingException` after max retries
- Do not rely only on manual reasoning for extraction logic correctness

## 7. Documentation coupling rules

Every change must update the relevant `rules/` files. Failure to update documentation means the task is incomplete.

| Change type | Files to update |
|---|---|
| New crawler | `rules/domain/crawling.md`, `rules/domain/product-parsing.md` |
| New API endpoint | `rules/api/endpoints.md` |
| New model field | `rules/database/schema.md`, `rules/api/endpoints.md` |
| New config key | `rules/database/schema.md` |
| New file-based state | `rules/database/schema.md` |
| New proxy behavior | `rules/domain/proxy-management.md` |

## 8. Forbidden behaviors

- ❌ Generating entire applications from scratch
- ❌ Editing files without reading them first
- ❌ Ignoring existing crawler conventions
- ❌ Hardcoding marketplace cookies or API keys in source
- ❌ Calling `sync_playwright()` in async context
- ❌ Skipping tests
- ❌ Skipping documentation
- ❌ Making speculative changes to other crawlers when only one is requested

## Checklist

- [ ] Relevant rules files read
- [ ] Existing crawler code inspected before writing
- [ ] Design proposed before implementation
- [ ] Minimal change applied (one file / one concern at a time)
- [ ] Tests added or updated
- [ ] Syntax verified (`py_compile`)
- [ ] Documentation updated
- [ ] No hallucinated API endpoints or field names introduced
- [ ] No hardcoded credentials
