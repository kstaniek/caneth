# Contributing to caneth

Thanks for helping improve **caneth**! ❤️

## Quick start

```bash
# 1) Set up a virtual environment and dev tools
make venv dev
source .venv/bin/activate
pre-commit install

# 2) Run the full QA suite
make check    # ruff format-check + ruff lint + mypy + tests
```

## Common tasks

- Format: `make format`
- Lint: `make lint`
- Format check (no changes): `make format-check`
- Type-check: `make type`
- Tests (quiet): `make test`
- Tests (verbose): `make test-verbose`
- Coverage (XML + terminal): `make coverage`
- Build package: `make dist` (then `make twine-check`)
- Build docs: `make docs` → serve: `make docs-serve`
- Quick CLI run: `make run HOST=172.31.11.67 PORT=20001`

> If you don’t have `make`, see the commands in the `Makefile`—they’re simple wrappers.

## Commit & PR guidelines

- Keep PRs focused and small where possible.
- Add/adjust tests for any behavior changes.
- Update README/API docs if you change public API.
- Ensure `make check` passes before submitting.

## Coding style

- Python ≥ 3.9, fully typed.
- **Ruff** handles formatting and linting:
  - `ruff format`
  - `ruff check`
- **mypy** in (near) strict mode:
  - `mypy caneth`

## Release flow (maintainers)

- Versions are tagged `vX.Y.Z`.
- Publishing uses **PyPI Trusted Publishing (OIDC)** via GitHub Actions.
- Draft notes are prepared by **Release Drafter**; cut a GitHub Release, push the tag.

## Security

Please report vulnerabilities privately via GitHub Security Advisories.  
See [SECURITY.md](SECURITY.md) for details.