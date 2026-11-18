# API contracts

Language-neutral source of truth for public API shapes.

- `schemas/*.vN.schema.json` — JSON Schema (draft 2020-12), versioned by suffix.
- `examples/*.json` — canonical payloads. **Both** the Python models
  (`careerpilot_common`) and the TypeScript Zod schemas (`@careerpilot/validation`)
  are tested against these files, so a drift on either side fails CI
  (`tests/contract/` and `packages/validation/src/*.test.ts`).

Versioning policy: additive, optional fields are non-breaking. Removing or
renaming a field, or tightening a constraint, requires a new `vN+1` schema and
a deprecation window for the old version.

Service OpenAPI documents are served at `/openapi.json` in non-production
environments and will be exported here per service as endpoints are added
(Phase 2+).
