"""Geospatial checks (Shapely).

* Coordinates are checked against the jurisdiction's approximate bounding box.
* If GeoJSON layers of sensitive features are placed in ``data/geospatial/``
  (properties: ``name``, ``category`` = protected_area | wetland | waterbody |
  settlement | heritage), the distance from the project point to the nearest
  feature of each category is computed.

No layers ship with EIAGen, and coordinates are never invented: if none are
supplied, proximity is reported as not assessed.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from shapely.geometry import Point, box, shape
from shapely.ops import nearest_points

from app.core.config import get_settings
from app.services.jurisdictions import get_jurisdiction

CATEGORY_FLAGS = {
    "protected_area": "near_protected_area",
    "wetland": "near_wetland_or_waterbody",
    "waterbody": "near_wetland_or_waterbody",
    "heritage": "near_cultural_heritage",
    "settlement": "near_settlement",
}
PROXIMITY_KM = 5.0


@dataclass
class GeoResult:
    notes: list[str]
    flags: list[str]
    within_jurisdiction: bool | None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _load_layers(directory: Path) -> list[dict]:
    features = []
    if not directory.exists():
        return features
    for path in sorted(directory.glob("*.geojson")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for feat in data.get("features", []):
            props = feat.get("properties") or {}
            try:
                features.append({"geometry": shape(feat["geometry"]), "name": props.get("name", "unnamed"),
                                 "category": props.get("category", "unknown"), "layer": path.name})
            except Exception:
                continue
    return features


def assess_location(jurisdiction: str, latitude: float | None, longitude: float | None) -> GeoResult:
    if latitude is None or longitude is None:
        return GeoResult(notes=["No coordinates supplied: proximity to sensitive areas was not assessed spatially."],
                         flags=[], within_jurisdiction=None)
    notes: list[str] = []
    flags: list[str] = []
    point = Point(longitude, latitude)
    within = None
    cfg = get_jurisdiction(jurisdiction) or {}
    bb = cfg.get("bounding_box")
    if bb:
        within = box(bb["min_lon"], bb["min_lat"], bb["max_lon"], bb["max_lat"]).contains(point)
        if not within:
            notes.append(f"Coordinates ({latitude:.4f}, {longitude:.4f}) fall outside the approximate bounding "
                         f"box of {cfg.get('name', jurisdiction)} - check the coordinates or the jurisdiction.")
    layers = _load_layers(get_settings().geospatial_dir)
    if not layers:
        notes.append("No geospatial layers are configured (data/geospatial/*.geojson): proximity to protected "
                     "areas, wetlands and settlements was not assessed spatially.")
        return GeoResult(notes=notes, flags=flags, within_jurisdiction=within)
    nearest: dict[str, tuple[float, str]] = {}
    for feat in layers:
        geom = feat["geometry"]
        p_near, g_near = nearest_points(point, geom)
        dist = 0.0 if geom.contains(point) else haversine_km(p_near.y, p_near.x, g_near.y, g_near.x)
        cat = feat["category"]
        if cat not in nearest or dist < nearest[cat][0]:
            nearest[cat] = (dist, feat["name"])
    for cat, (dist, name) in sorted(nearest.items()):
        notes.append(f"Nearest {cat.replace('_', ' ')}: {name} at approximately {dist:.1f} km (spatial layer).")
        if dist <= PROXIMITY_KM and cat in CATEGORY_FLAGS:
            flags.append(CATEGORY_FLAGS[cat])
    return GeoResult(notes=notes, flags=sorted(set(flags)), within_jurisdiction=within)
