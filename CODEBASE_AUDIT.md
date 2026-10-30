# Codebase Status

This page describes the implemented and locally verified scope of the repository. It is not a production-readiness, market, or customer validation.

## What works

- The backend parses Python source with the Python AST and builds a dependency graph with NetworkX.
- The bundled ShopLite sample supports the pytest fault-injection workflow.
- The latest local test runs completed successfully: 71 backend tests and 18 frontend tests.

## What does not (or is not verified)

- The application has no authentication.
- There is no customer traction, customer validation, or pricing evidence.
- Code analysis is Python-only.
- The Docker sandbox path is present but has not been tested; its isolation is not verified.
- Gemma is optional and is disabled when no API key is configured.

## Test verification

Verified locally with `python -m pytest -q` from `backend/` and `npm test -- --reporter=dot` from `frontend/`. Results: 71 backend tests passed and 18 frontend tests passed.
