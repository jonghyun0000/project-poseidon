"""Natural Earth 1:10 million land screen. Map-scale land, never an ENC.

https://www.naturalearthdata.com/downloads/10m-physical-vectors/10m-land/
Point sampling at <=1 nmi can miss small islands/channels. No depths/clearance.
"""
from functools import lru_cache

import numpy as np
import shapefile
import shapely
from shapely.geometry import shape

from poseidon.core.config import settings


@lru_cache(maxsize=1)
def land_index():
    path = settings.data_root / 'static/natural-earth-10m/ne_10m_land.shp'
    if not path.exists():
        return None
    with shapefile.Reader(str(path)) as reader:
        return shapely.STRtree([shape(s.__geo_interface__) for s in reader.iterShapes()])


def land_points(latitudes, longitudes, index=None):
    index = land_index() if index is None else index
    if index is None:
        return None
    lon = (np.asarray(longitudes)+180) % 360-180
    hits = index.query(shapely.points(lon, latitudes), predicate='intersects')
    mask = np.zeros(len(lon), dtype=bool)
    if hits.size:
        mask[hits[0]] = True
    return mask


def global_screening(geometry):
    points = geometry['points']
    mask = land_points([p['lat'] for p in points], [p['lon'] for p in points])
    bad = [dict(lat=p['lat'], lon=p['lon'], leg=p['leg']) for p, hit in zip(points, mask) if hit] if mask is not None else []
    return {'status': 'unavailable' if mask is None else 'land_detected' if bad else 'no_land_detected',
            'land_count': len(bad), 'unknown_count': len(points) if mask is None else 0,
            'points': bad[:30], 'sample_spacing_nm_max': 1,
            'source': 'Natural Earth land v5.1.1 / 1:10 million map scale',
            'note': '전 지구 육지 표본 검사. 작은 섬·수로·수심 여유·항로 규제·공식 해도는 검증하지 않습니다.'}
