# CI/CD Rules

> Purpose: Ensure every change passes automated checks and deployments consistently. Guarantee that AI-generated code is validated and deployable before reaching production.
> Context: Read this file before touching any pipeline, deployment script, or merge process.
> Version: 1.0

## 1. Core principle

Every change must pass automated checks before merging or deploying.

- CI/CD is not optional
- No manual deployments without pipeline completion
- Broken pipelines must be fixed immediately
- Never disable checks to speed up delivery

_Not applicable — this project does not currently have a CI/CD pipeline configured (no `.github/`, `Dockerfile`, or deployment scripts were found in the codebase). The rules below define the mandatory standard when a pipeline is added._

## 2. Current deployment method

The service is started manually:

```bash
# Activate virtual environment
source venv/bin/activate
pip install -r requirements.txt
patchright install
python api.py
```

Configuration is in `data/settings.yml`. Proxy list is in `data/proxy.txt`. These files are not committed to version control (data files).

## 3. Pipeline stages (required when CI/CD is introduced)

**3.1 Pre-commit / local checks**

Git hooks live in `linting/hooks/` and are enabled with `make hooks` (`git config core.hooksPath linting/hooks`). Each hook calls a script in `linting/`:
- `pre-commit` → `linting/pre-commit-check.sh`: refuses commits on a branch other than `issues-N` (detached HEAD during rebase is allowed); `ruff check` on staged `.py` files; `mypy` when a staged file is in a package from `pyproject.toml` `[tool.mypy] files`; warns when a staged `services/<name>.py` has no `tests/test_<name>.py`
- `pre-push` → `linting/pre-push-check.sh`: `ruff check .` and the full `pytest` suite
- `prepare-commit-msg` → `linting/prepare-commit-msg-check.sh`: generates a message with Claude Code only for a plain `git commit` without `-m`; prefixes the title with `issues-N|` from the branch; adds a `Files:` block before trailers such as `Co-Authored-By`. Merge, squash and amend messages are left as they are
- Fail early if any error occurs; never bypass with `--no-verify`

**3.2 Continuous Integration (CI)**

Triggered on every push or pull request. Must include:
- Dependency installation: `pip install -r requirements.txt`
- Syntax checks: `python -m py_compile **/*.py`
- Linting: `flake8 .` or `ruff check .`
- Unit tests: `pytest tests/`
- Type check: `mypy` on the packages listed in `pyproject.toml` `[tool.mypy] files`. A package is added to the list once its mypy errors are fixed; never remove one to make CI green
- No secrets committed (scan `data/settings.yml` out of CI)

No merge without CI passing.

**3.3 Continuous Delivery (CD)**

- `data/settings.yml` must be supplied via environment secrets or a secrets manager — never committed with production values
- `data/proxy.txt` must be supplied via secrets injection — never committed
- The `api_key` in `settings.yml` must be rotated between environments

## 4. Dependency management rules

```
# ✅ Correct — pinned versions in requirements.txt
loguru==0.7.3
fastapi==0.116.1
pydantic

# ❌ Incorrect — unpinned versions
loguru
fastapi>=0.100
```

All production dependencies must have pinned versions. `pydantic` is currently unpinned and should be pinned before any CI pipeline is added.

## 5. Rules for AI involvement

- AI must never merge directly into protected branches
- AI may propose pipeline configuration only after reading this file
- AI-generated GitHub Actions YAML must be validated with a YAML linter before commit
- AI must not add new dependencies to `requirements.txt` without documenting why

## 6. Secrets rules

- `data/settings.yml` must not be committed with real API keys
- `data/proxy.txt` must not be committed
- CI environments must inject these via secrets or encrypted variables
- Example/template files (`settings.example.yml`) may be committed

## 7. Forbidden actions

- ❌ Skipping tests or linters
- ❌ Committing `data/proxy.txt` or `data/settings.yml` with real secrets
- ❌ Deploying without dependency installation
- ❌ Ignoring failed jobs

## 8. Example pipeline (when GitHub Actions is added)

```yaml
# ✅ Correct — lint and test before any deployment step
name: CI
on: [push, pull_request]
jobs:
  lint-and-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: "3.11"
      - run: pip install -r requirements.txt
      - run: python -m py_compile api.py config.py services/parsing.py
      - run: pytest tests/ -v
```

```yaml
# ❌ Incorrect — deploy before tests
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - run: python api.py &
```

## 9. Releases

- The app version lives only in the `VERSION` file in the repo root; `config.py` reads it into `settings["soft_version"]` (API version in `/openapi.json`, every log line)
- Release order: a PR bumps `VERSION` to the new version, it is merged into `main`, then the tag `vX.Y.Z` and the GitHub Release are created from `main`
- `tests/test_version.py` fails when `VERSION` is lower than the latest `v*` tag, so a release whose version was not bumped is caught by CI (the test job checks out tags for this)

## 10. Nightly live check

- `.github/workflows/live-check.yml` runs `scripts/live_check.py` every night at 03:00 UTC and on demand (Actions → Live check → Run workflow): one reference product per marketplace is parsed on the live site and must have a title and a price above zero
- It never runs on PRs and never blocks a merge — it reports that a marketplace changed, not that a PR is wrong. On failure it opens (or comments on) the issue "Live check: crawlers failing"
- Russian proxies come from the `GREX_PROXY` secret (one per line), the type from the `GREX_PROXY_TYPE` variable (`http` by default)
- Reference products live in `PRODUCTS` in `scripts/live_check.py`; replace a product that went out of stock instead of disabling the check

## Checklist before merging

- [ ] All `.py` files syntax-valid
- [ ] No secrets committed (`data/proxy.txt`, `data/settings.yml` with real keys)
- [ ] `requirements.txt` versions pinned
- [ ] Tests pass locally
- [ ] If pipeline exists: CI passes
- [ ] Documentation updated if pipeline changed
