"""Show where each crosswalk anchor resolves in the ingested official texts.

    python scripts/check_crosswalk.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.database import init_db, session_scope  # noqa: E402
from app.services.crosswalk import crosswalk, resolve_anchor  # noqa: E402


def main() -> int:
    init_db()
    unresolved = 0
    with session_scope() as db:
        for summary_id, entry in crosswalk().items():
            print(f"\n{summary_id} -> {entry['official']}")
            for section, anchor in entry["sections"].items():
                p = resolve_anchor(db, entry["official"], anchor)
                if p is None:
                    unresolved += 1
                    print(f"  UNRESOLVED  {section!r}: {anchor!r}")
                else:
                    preview = " ".join(p.text.split())[:110]
                    print(f"  p.{p.page or '-':>3} chunk {p.chunk_id:<6} {section[:48]:48} | {preview}")
    print(f"\n{unresolved} unresolved anchors")
    return 1 if unresolved else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
