"""Register a project from a JSON file (or use an existing one) and run the screening workflow.

    python scripts/run_screening.py --project-file examples/mineral_processing_ghana.json
    python scripts/run_screening.py --project-id 1
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agents.orchestrator import run_screening  # noqa: E402
from app.database import init_db, session_scope  # noqa: E402
from app.models import Project  # noqa: E402
from app.schemas import ProjectCreate  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--project-file", type=Path)
    g.add_argument("--project-id", type=int)
    args = p.parse_args()
    init_db()
    with session_scope() as db:
        if args.project_file:
            data = ProjectCreate.model_validate(json.loads(args.project_file.read_text(encoding="utf-8")))
            project = Project(name=data.name, developer=data.developer, description=data.description,
                              sector=data.sector, phase=data.phase, objectives=data.objectives, location=data.location,
                              jurisdiction=data.jurisdiction, region=data.region,
                              latitude=data.coordinates.latitude if data.coordinates else None,
                              longitude=data.coordinates.longitude if data.coordinates else None,
                              characteristics=data.characteristics, environmental_setting=data.environmental_setting)
            db.add(project)
            db.flush()
            print(f"Registered project {project.id}: {project.name}")
        else:
            project = db.get(Project, args.project_id)
            if project is None:
                sys.exit(f"Project {args.project_id} not found")
        result = run_screening(db, project)
        print(f"\nScreening version {result.version}: {result.status}")
        for s in result.stages:
            print(f"  {s.stage:38} {s.status:9} {s.duration_ms or 0:8.0f} ms  {s.message or ''}")
        if result.status != "completed":
            sys.exit(result.error)
        print("\nOutcome:", "; ".join(result.assessment.outcome_labels))
        print("Regulatory screening:", result.assessment.regulatory_screening.summary)
        print(f"Impacts: {len(result.impacts)} | Requirements: {len(result.requirements)} | "
              f"Citations valid: {result.validation.citations_valid}/{result.validation.citations_checked}")
        print("Review state:", result.review_state.value)


if __name__ == "__main__":
    main()
