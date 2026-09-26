"""Read-only worldwide port catalog API; network acquisition is an explicit build step."""
from fastapi import APIRouter, HTTPException, Query
from poseidon.ports.catalog import CatalogUnavailable, catalog_meta, get_port, ports_geojson, search_ports

router = APIRouter()


@router.get('/v1/ports/meta')
def port_meta():
    return catalog_meta()


@router.get('/v1/ports')
def port_search(q: str = Query('', max_length=160),
                country: str | None = Query(None, pattern=r'^[A-Za-z]{2}$'),
                limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0)):
    try:
        return search_ports(q, country, limit, offset)
    except CatalogUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get('/v1/ports/geojson')
def port_map(country: str | None = Query(None, pattern=r'^[A-Za-z]{2}$')):
    try:
        return ports_geojson(country)
    except CatalogUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get('/v1/ports/{port_id}')
def port_detail(port_id: str):
    try:
        result = get_port(port_id)
    except CatalogUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    if result is None:
        raise HTTPException(404, '해당 항만 식별자가 없습니다.')
    return result
