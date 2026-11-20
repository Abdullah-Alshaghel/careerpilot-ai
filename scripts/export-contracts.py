"""Write JSON Schemas generated from the services' Pydantic models into shared/.

    python scripts/export-contracts.py           # regenerate
    python scripts/export-contracts.py --check   # fail if any file is stale (CI / tests)

Hand-written contracts (health, auth) are not touched. The TypeScript Zod
schemas are checked against the same canonical examples.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "shared" / "api-contracts" / "schemas"
EVENTS = ROOT / "shared" / "event-schemas" / "payloads"
BASE_ID = "https://contracts.careerpilot.ai/"


def generated() -> dict[Path, dict]:
    from career_profile_service.schemas import ProfileOut
    from company_service.schemas import CompanyList, CompanyOut
    from document_service.schemas import DocumentDeletedV1, DocumentList, DocumentOut, DocumentTextExtractedV1
    from job_discovery_service.schemas import JobDiscoveredV1, JobList, JobOut, SearchRunList, SearchRunOut

    targets = {
        API / "document.v1.schema.json": (DocumentOut, "api/document.v1.schema.json"),
        API / "document-list.v1.schema.json": (DocumentList, "api/document-list.v1.schema.json"),
        API / "career-profile.v1.schema.json": (ProfileOut, "api/career-profile.v1.schema.json"),
        API / "company.v1.schema.json": (CompanyOut, "api/company.v1.schema.json"),
        API / "company-list.v1.schema.json": (CompanyList, "api/company-list.v1.schema.json"),
        API / "search-run.v1.schema.json": (SearchRunOut, "api/search-run.v1.schema.json"),
        API / "search-run-list.v1.schema.json": (SearchRunList, "api/search-run-list.v1.schema.json"),
        API / "job.v1.schema.json": (JobOut, "api/job.v1.schema.json"),
        API / "job-list.v1.schema.json": (JobList, "api/job-list.v1.schema.json"),
        EVENTS / "job.discovered.v1.schema.json": (JobDiscoveredV1, "events/payloads/job.discovered.v1.schema.json"),
        EVENTS / "document.text_extracted.v1.schema.json": (
            DocumentTextExtractedV1,
            "events/payloads/document.text_extracted.v1.schema.json",
        ),
        EVENTS / "document.deleted.v1.schema.json": (
            DocumentDeletedV1,
            "events/payloads/document.deleted.v1.schema.json",
        ),
    }
    out = {}
    for path, (model, schema_id) in targets.items():
        schema = model.model_json_schema(mode="serialization")
        out[path] = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": BASE_ID + schema_id,
            "x-generated-from": f"{model.__module__}.{model.__name__} (scripts/export-contracts.py)",
            **schema,
        }
    return out


def render(schema: dict) -> str:
    return json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    stale = []
    for path, schema in generated().items():
        text = render(schema)
        if args.check:
            if not path.exists() or path.read_bytes().decode("utf-8") != text:
                stale.append(path.relative_to(ROOT).as_posix())
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode("utf-8"))  # exact bytes: LF on every OS
            print(f"wrote {path.relative_to(ROOT).as_posix()}")
    if stale:
        print("stale contracts (run python scripts/export-contracts.py):", *stale, sep="\n  ", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
