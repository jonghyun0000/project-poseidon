# poseidon.ingest

데이터 수집: adapters/(소스별 SourceAdapter 구현) → qc/(QCCheck, 원값 불변·플래그 부여) → sync. 표준화 규약: CF 변수명, UTC, lineage attrs 필수.

Phase 23: `global_wave.py`는 별도 전 지구 NOAA GFS-Wave 수신기다.
지역 카탈로그/동결 모델에 섞지 않고 `data/global/gfswave`에 0.25°·6 h·384 h 원천과 해시/요청 기록을 저장한다.
전체 시각 완료 후 원자적으로 공개하며 실패 시 이전 완료 사이클을 유지한다. CLI와 API 갱신은 프로세스 잠금으로 중복을 막는다.
