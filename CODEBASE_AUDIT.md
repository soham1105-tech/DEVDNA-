# Codebase Status

This page describes the implemented and locally verified scope of the repository. It is not a production-readiness, market, or customer validation.

## What works

- The backend parses Python source with the Python AST and builds a dependency graph with NetworkX.
- The bundled ShopLite sample supports the pytest fault-injection workflow.
- The latest local test runs completed successfully: 139 backend tests (1 skipped: the Docker sandbox test, skipped when Docker is unavailable) and 22 frontend tests.

## What does not (or is not verified)

- The application has no authentication.
- There is no customer traction, customer validation, or pricing evidence.
- Code analysis is Python + JS/TS.
- The Docker sandbox is tested in CI, skipped when unavailable.
- Gemma is optional and is disabled when no API key is configured.

## Test verification

Verified locally with `python -m pytest -q` from `backend/` and `npm test -- --reporter=dot` from `frontend/`. Results: 139 backend tests passed (1 skipped) and 22 frontend tests passed.
