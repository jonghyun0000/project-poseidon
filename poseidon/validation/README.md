# poseidon.validation

metrics/(RMSE·bias·SI·CRPS·skill) · pipelines/(예보-관측 콜로케이션 → error_sample). 보고서 산출물은 /reports.

Phase 24: `global_observations.py`는 NDBC 공개 실시간 관측·배치 이력을 스냅숏으로 보존한다.
`global_metrics.py`는 표본/관측소 가중, 고유 관측, 사이클 군집, 리드·지리 구역·파고대를 집계한다.
`global_audit.py`는 동결 규약과 원본 해시를 남기며 별도 NOAA 예보 대조를 실행한다.
`global_altimeter.py`는 별도 Sentinel-3A 원양 구간 검증이다. 부이와 위성 점수를 합치지 않는다.
두 경로 모두 지역 `error_sample`, AI 보정 및 동결 물리 엔진을 변경하지 않는다.
새 결과는 새 실행 ID로 저장하고 완료 후 최신 포인터를 바꾼다. 재현은 각 모듈의 `--resume <run_id> --offline`.
방법과 실제 결과: `docs/PHASE24_GLOBAL_OBSERVATIONAL_VALIDATION.md`와 위성 방법 문서.
