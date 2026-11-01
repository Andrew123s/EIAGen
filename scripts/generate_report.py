"""(Re)generate the preliminary screening report for a project's latest screening.

    python scripts/generate_report.py --project-id 1
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.database import init_db, session_scope  # noqa: E402
from app.services.findings_service import latest_run  # noqa: E402
from app.services.report_service import generate_report  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project-id", type=int, required=True)
    args = p.parse_args()
    init_db()
    with session_scope() as db:
        run = latest_run(db, args.project_id)
        if run is None:
            sys.exit("No completed screening for this project; run scripts/run_screening.py first")
        report = generate_report(db, run)
        print(f"Report version {report.version} (screening v{run.version}, state {run.review_state})")
        for label, path in (("DOCX", report.docx_path), ("PDF", report.pdf_path), ("Markdown", report.markdown_path)):
            print(f"  {label:8} {path}")


if __name__ == "__main__":
    main()
