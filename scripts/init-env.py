"""Create or complete the local `.env` from `.env.example` with fresh secrets.

    python scripts/init-env.py            # create .env (refuses to overwrite)
    python scripts/init-env.py --update   # add keys missing from an existing .env

Every `change-me-*` placeholder becomes a random URL-safe value, and
JWT_SIGNING_KEY becomes a new Ed25519 seed. Existing values are never changed
and secrets are never printed. Used by CI to generate throwaway secrets.
"""

from __future__ import annotations

import argparse
import base64
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_ASSIGNMENT = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")


def _secret_for(name: str, placeholder: str) -> str:
    if name == "JWT_SIGNING_KEY":
        return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
    if placeholder.startswith("change-me-"):
        return secrets.token_hex(16)
    return placeholder


def _render(lines: list[str]) -> list[str]:
    out = []
    for line in lines:
        match = _ASSIGNMENT.match(line)
        if match:
            name, value = match.groups()
            line = f"{name}={_secret_for(name, value)}"
        out.append(line)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--update", action="store_true", help="append keys missing from an existing .env")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    args = parser.parse_args()

    example = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    target: Path = args.env_file

    if not target.exists():
        target.write_text("\n".join(_render(example)) + "\n", encoding="utf-8")
        print(f"created {target.name} with generated secrets")
        return 0
    if not args.update:
        print(f"{target.name} already exists; use --update to add missing keys", file=sys.stderr)
        return 1

    current = target.read_text(encoding="utf-8")
    present = {m.group(1) for m in map(_ASSIGNMENT.match, current.splitlines()) if m}
    missing = [line for line in _render(example) if (m := _ASSIGNMENT.match(line)) and m.group(1) not in present]
    if not missing:
        print(f"{target.name} is complete")
        return 0
    with target.open("a", encoding="utf-8") as fh:
        if not current.endswith("\n"):
            fh.write("\n")
        fh.write("\n".join(missing) + "\n")
    names = ", ".join(_ASSIGNMENT.match(line).group(1) for line in missing)  # type: ignore[union-attr]
    print(f"added to {target.name}: {names}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
