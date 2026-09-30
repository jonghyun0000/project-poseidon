"""Local map-scale coast geometry for the interactive helm, never a nautical chart."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from shapely import affinity
from shapely.geometry import box, mapping

from poseidon.twin.global_coast import land_index

router = APIRouter()
RADIUS_DEGREES = 0.03


@router.get('/v1/helm/coast')
def helm_coast(
    lat: float = Query(ge=-80, le=80),
    lon: float = Query(ge=-180, le=180),
) -> dict:
    tree = land_index()
    if tree is None:
        raise HTTPException(status_code=503, detail='Natural Earth land geometry unavailable')
    features = []
    for shift in (-360, 0, 360):
        window = box(lon - RADIUS_DEGREES - shift, lat - RADIUS_DEGREES,
                     lon + RADIUS_DEGREES - shift, lat + RADIUS_DEGREES)
        for index in tree.query(window):
            clipped = tree.geometries[index].intersection(window)
            if clipped.is_empty:
                continue
            if shift:
                clipped = affinity.translate(clipped, xoff=shift)
            features.append(mapping(clipped))
    return {
        'status': 'ready', 'center': [lon, lat], 'radius_degrees': RADIUS_DEGREES,
        'source': 'Natural Earth 1:10m land v5.1.1',
        'limitation': 'Map-scale screening only; small islands, shoreline detail, depths and ENC are unverified.',
        'geometries': features,
    }
