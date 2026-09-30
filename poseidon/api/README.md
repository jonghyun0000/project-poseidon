# poseidon.api

FastAPI 앱: REST/WS/타일 (docs/api/openapi.yaml이 계약).


Phase 21: `voyage.py`는 `POST /v1/voyage/analyze`의 입력 검증·서비스 연결을 담당한다.
실행 중 `/docs`가 구현된 요청 스키마를 제공한다. 정적 openapi.yaml은 과거 설계 초안이므로 실제 경로와 다르다.

Phase 22: 선택 입력 `arrival_deadline_utc`, `performance_profile`과 동일 사이클 GFS 바람 연결.
응답 `voyage-1.1`에 바람 출처·결측, 연료/CO₂ 범위·출처, 도착 마감 판정을 추가했다.
재현 요청은 `docs/api/voyage-environment-request.example.json` 및 명시적 synthetic 예제를 참조한다.

Phase 23: `global_ocean.py`의 `/v1/global/{meta,point,hs.png,status}`와
`global_voyage.py`의 전 지구 항로 분석. 기존 항로 요청에 `source=global`을 명시하면 전 지구로 분기한다.
기본 요청 source는 regional이며 기존 API 계약을 보존한다. NOAA의 독립 검증과 지역 성적은 구분한다.

Phase 24: `global_validation.py`의 `/v1/global/validation`(부이)와
`/v1/global/validation/altimeter`(위성)는 서로 분리된 외부 관측 대조 결과다.
각 경로 아래 `/report.json`, `/pairs.csv`가 실행 ID를 고정한 다운로드를 제공한다.
결과가 없으면 unavailable이며 0 오차를 만들지 않는다. 표본 존재로 지점·항로를 산업용으로 승급하지 않는다.

Phase 25: `ports.py`의 `/v1/ports`, `/meta`, `/geojson`, `/{port_id}`는 검증된 로컬 카탈로그를 읽는다.
`port_voyage.py`는 `Waypoint.port_id`를 단일 카탈로그 스냅샷의 정식 좌표와 검사하고 이름·원천·해시를 확정한다.
항만 연결을 포함하면 `voyage-1.3` 응답에 `port_references`를 저장한다. 좌표 불일치422, 목록 미확보503.

Phase 26: `routing.py`는 항구 쌍을 검증하고 고정 Eurostat 해상망에서 계획을 생성·저장한다.
`/v1/routing/{meta,plan,plans/{plan_id}}`, `routing_voyage.py`는 저장 계획의 모든 좌표·정책·해안선과 거리·육지 교차를 검증한다.
자동 항로는 `route_plan_id`와 최대 5,000점, 수동은 기존 20점. `voyage-1.4`에 전체 `route_plan`,
`scope=network_segment_only`를 반환한다. 미검증 항구 접속 간격은 계산에서 제외하며 항만 도착 판정은 미적용이다.

Phase 33: `helm_coast.py`의 `/v1/helm/coast`는 Natural Earth 1:10m 육지를 선박 주변 0.03°로 잘라 반환한다. 지도 축척 교차 검사 전용이며 공식 ENC·수심 자료가 아니다.
