"""Read-only immutable-run global Hs verification reports and paired exports."""
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from poseidon.ingest.global_wave import read_json
from poseidon.core.config import settings
from poseidon.validation.global_audit import root, latest_report

router = APIRouter()


def altimeter_root():
    return settings.data_root/'validation'/'global_altimeter'


def report_for(run_id=None, satellite=False):
    if run_id is None and not satellite:
        return latest_report()
    folder = altimeter_root() if satellite else root()
    if run_id is None:
        latest = read_json(folder/'latest.json')
        run_id = latest.get('run_id') if isinstance(latest,dict) else None
        if not isinstance(run_id,str) or not re.fullmatch(r'\d{8}T\d{6}Z',run_id):
            return None
    if not re.fullmatch(r'\d{8}T\d{6}Z',run_id):
        raise HTTPException(422,'검증 실행 식별자 형식이 올바르지 않습니다.')
    report = read_json(folder/'runs'/run_id/'report.json')
    if not report:
        raise HTTPException(404,'해당 검증 보고서가 없습니다.')
    return report


@router.get('/v1/global/validation')
def global_validation(run_id: str | None=None):
    report = report_for(run_id)
    if not report:
        return {'schema_version':'global-validation-1.0','state':'unavailable',
                'message':'전 지구 원천의 관측 대조 결과를 아직 확보하지 못했습니다.',
                'job':read_json(root()/'status.json')}
    return {**report,'job':read_json(root()/'status.json')}


def artifact(name, run_id, media_type, satellite=False):
    report = report_for(run_id,satellite)
    if not report:
        raise HTTPException(404,'완료된 검증 산출물이 없습니다.')
    path = (altimeter_root() if satellite else root())/'runs'/report['run_id']/name
    if not path.is_file():
        raise HTTPException(404,'검증 산출물 파일이 없습니다.')
    return FileResponse(path,media_type=media_type,
                        filename=f"global-validation-{report['run_id']}-{name}")


@router.get('/v1/global/validation/report.json')
def global_validation_report(run_id: str | None=None):
    return artifact('report.json',run_id,'application/json')


@router.get('/v1/global/validation/pairs.csv')
def global_validation_pairs(run_id: str | None=None):
    return artifact('pairs.csv',run_id,'text/csv')


@router.get('/v1/global/validation/altimeter')
def global_altimeter_validation(run_id: str | None=None):
    report = report_for(run_id,satellite=True)
    if not report:
        return {'schema_version':'global-altimeter-validation-1.0','state':'unavailable',
                'message':'외해 위성 관측 대조 결과를 아직 확보하지 못했습니다.',
                'job':read_json(altimeter_root()/'status.json')}
    return {**report,'job':read_json(altimeter_root()/'status.json')}


@router.get('/v1/global/validation/altimeter/report.json')
def global_altimeter_report(run_id: str | None=None):
    return artifact('report.json',run_id,'application/json',satellite=True)


@router.get('/v1/global/validation/altimeter/pairs.csv')
def global_altimeter_pairs(run_id: str | None=None):
    return artifact('pairs.csv',run_id,'text/csv',satellite=True)
