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
- Run `python -m py_compile` on all `.py` files
- Run `flake8` or `ruff` for linting
- Run `pytest` for unit tests
- Fail early if any error occurs

**3.2 Continuous Integration (CI)**

Triggered on every push or pull request. Must include:
- Dependency installation: `pip install -r requirements.txt`
- Syntax checks: `python -m py_compile **/*.py`
- Linting: `flake8 .` or `ruff check .`
- Unit tests: `pytest tests/`
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

## Checklist before merging

- [ ] All `.py` files syntax-valid
- [ ] No secrets committed (`data/proxy.txt`, `data/settings.yml` with real keys)
- [ ] `requirements.txt` versions pinned
- [ ] Tests pass locally
- [ ] If pipeline exists: CI passes
- [ ] Documentation updated if pipeline changed
