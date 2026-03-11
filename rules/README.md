# Rules Directory — Master Orchestrator

> **Purpose:** Serve as the single entry point for AI agents to understand all mandatory rules, dependencies, and execution order.
> **Context:** Read this file first. Follow the steps exactly before touching any code, schema, or documentation.
> **Version:** 1.0

## 1. Core Principle

Before any task:

1. Understand the rules directory structure
2. Identify which files are relevant to your task
3. Follow rules strictly in order
4. Produce complete and auditable output

> The agent **must never skip reading any required file**. All domain, process, and meta rules are part of the source of truth.

## 2. Project Overview

This project is a **Python web scraping/parsing service** built with FastAPI. It provides a unified HTTP API to extract product card data (title, SKU, prices, images) from Russian e-commerce marketplaces: Wildberries, Ozon, Yandex Market, and MegaMarket.

**Stack:** Python 3, FastAPI, Pydantic v2, Loguru, curl-cffi, Patchright (Playwright), BeautifulSoup4, lxml, PyYAML, uvicorn

## 3. Directory Overview

```
rules/
├─ README.md                        ← You are here
├─ _meta/
│  └─ how-to-write-rules.md         ← File structure and writing standards
├─ database/
│  └─ schema.md                     ← Data model documentation (no SQL DB — in-memory/file-based)
├─ domain/
│  ├─ crawling.md                   ← Crawler selection, execution, and bot-bypass rules
│  ├─ product-parsing.md            ← ProductCard data extraction and normalization rules
│  └─ proxy-management.md          ← Proxy loading, validation, and rotation rules
├─ api/
│  └─ endpoints.md                  ← API contract rules
└─ process/
   ├─ architecture-design.md        ← Design before implementation
   ├─ observability.md              ← Logging, metrics, monitoring
   ├─ ai-workflow.md                ← AI agent lifecycle rules
   ├─ ci-cd.md                      ← CI/CD and pipeline rules
   ├─ security.md                   ← Security and safe coding rules
   └─ testing-strategy.md           ← Test strategy and coverage rules
```

## 4. Reading & Execution Order

When performing a task, the AI agent **must follow this sequence**:

| # | File | Purpose |
|---|------|---------|
| 1 | `_meta/how-to-write-rules.md` | Understand formatting, structure, and standards for all rules files |
| 2 | `process/architecture-design.md` | Perform pre-implementation design. Document diagrams, affected layers, boundaries |
| 3 | `process/ai-workflow.md` | Review lifecycle rules: understand, design, implement, verify, document |
| 4 | `database/schema.md` | Read data model before touching models, serialization, or file-based state |
| 5 | `domain/crawling.md` | Read crawler rules before adding or modifying any crawler |
| 6 | `domain/product-parsing.md` | Read product data rules before changing extraction or normalization logic |
| 7 | `domain/proxy-management.md` | Read proxy rules before modifying ProxyManager or proxy configuration |
| 8 | `api/endpoints.md` | Understand API contracts. Do not implement endpoints not defined here |
| 9 | `process/observability.md` | Ensure logs and request tracing are included |
| 10 | `process/ci-cd.md` | Validate pipeline rules |
| 11 | `process/security.md` | Read security rules before modifying auth, input handling, or secrets |
| 12 | `process/testing-strategy.md` | Ensure tests cover all changes and critical paths |

> **Rule:** Never implement before design and lifecycle steps are confirmed.

## 5. Self-Verification

Before reporting task completion, the agent **must check**:

- [ ] All relevant rules files read and applied
- [ ] Design artifacts exist if required
- [ ] Data model, domain, API changes validated against rules
- [ ] Logging confirmed in new crawler or service code
- [ ] Security rules enforced (API key auth, input validation)
- [ ] Tests created or updated as mandated
- [ ] No forbidden behaviors introduced
- [ ] Documentation updated in `rules/` as needed

## 6. Reporting / Task Output

At the end of a task, the AI agent **must produce a structured report**:

1. **Files affected** — List all files touched
2. **Rules read** — Confirm each relevant rules file was read
3. **Design artifacts** — Include diagrams, impact analysis, or placeholders
4. **Verification checks** — List all checklist items passed
5. **Deviations** — Document any items skipped or marked `_Not applicable_`
6. **Summary** — State task completion, blockers, and next steps

**Example:**

```json
{
  "task": "Add new Lamoda crawler",
  "files_affected": ["crawlers/lamoda.py", "core/factory.py", "crawlers/__init__.py"],
  "rules_read": [
    "process/architecture-design.md",
    "domain/crawling.md",
    "domain/product-parsing.md",
    "api/endpoints.md"
  ],
  "design_artifacts": ["crawler-flow-diagram"],
  "verification_checks": [
    "✅ Design approved",
    "✅ Tests created",
    "✅ Factory registered"
  ],
  "deviations": ["None"],
  "summary": "Task complete and ready for merge"
}
```

## 7. Best Practices

- Always create placeholders for non-existent domains or files
- Never assume defaults — follow explicit rules
- Stop immediately if a required rules file is missing
- Maintain consistency with existing code and rules
- Incremental and verifiable steps are mandatory

## Agent Entry Point Summary

| Step | Action |
|------|--------|
| 1 | Read this `README.md` first |
| 2 | Follow the reading & execution order strictly |
| 3 | Apply rules and document all steps |
| 4 | Verify using checklist in Section 5 |
| 5 | Produce structured task report as in Section 6 |
