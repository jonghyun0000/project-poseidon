# 해상 항로망 후보 계산

Phase 26. 세계 항구 두 곳을 공개 해상 항로망에 연결하고 육지 검사를 통과한
네트워크 경로를 거리 기준으로 계산한다. 상태는 `candidate`, 범위는 `network_segment_only`다.
검증된 항만 입출항 경로나 항해 지침을 제공하는 모듈은 아니다.
전체 방법·제약·재현은 `docs/PHASE26_AUTOMATIC_SEA_ROUTES.md`를 읽는다.

## 파일 경계

| 파일 | 책임 |
|---|---|
| `network.py` | 고정 commit·SHA·좌표·간선·꼭짓점 수를 대조하여 원본 그래프 로드 |
| `engine.py` | WGS84 거리, 2 nm 측지선 세분화와 육지 다각형 교차, 접속 정점, Dijkstra, 완성 형상 재검사 |
| `store.py` | 정규 JSON SHA 기반 계획 저장·읽기와 파일 무결성 검사 |
| `poseidon/api/routing.py` | 카탈로그 항구 입력 검증, 계획 생성·원천 메타·저장 계획 API |
| `poseidon/api/routing_voyage.py` | 계획과 분석 입력 좌표·범위·해안선·정책 재검사 |
| `data/static/routing/` | 원본 GPKG, 변환 JSON, EUPL-1.2, 원천 설명, 재현 스크립트, 해시 |

`load_network()`는 캐시된 `{manifest, nodes, edges}`를 돌려준다. 반환값을 수정하지 않는다.
`nodes`는 `{lat, lon}`, 간선은 `{id, u, v, coordinates, passage}`다.
간선 `id`는 원본 fid, `u/v`는 정점 인덱스, `coordinates`는 모든 `[lon, lat]` 꼭짓점이다.
무방향 간선이며 모든 꼭짓점을 따른 거리를 계산해야 한다. 끝점만 이어서는 안 된다.

## 원천과 제한

Eurostat SeaRoute 공식 commit `88a2e568a8e0144d1f5a81c3931a7bc2bcce6901`.
커밋 날짜 2022-01-10 / GPKG 내부 자료 시점 2021-09-08 / 확보일 2026-09-12 UTC.
원본과 변환 데이터의 라이선스는 EUPL-1.2이며 원문을 함께 보존한다.
36,109개 정점, 72,478개 간선, 151,169개 원본 꼭짓점이다.
12개 동일 위도 날짜변경선 쌍만 같은 정점으로 연결하며 떨어진 끝점을 임의 연결하지 않는다.

육지 원천은 Natural Earth v5.1.1 / 1:10 million이다.
2 nm 간격 측지선 근사 폴리라인의 **전체 선**과 다각형을 검사한다.
70,173개 간선이 이번 고정 자료에서 통과했지만 해도 수준 항행 적합성 검증 수는 아니다.
자료·검사 정책·저장 캐시 해시가 일치해야 캐시를 재사용한다.

수에즈·파나마·킬·코린트 운하와 북서·북동 극지 통로를 항상 제외한다.
말라카·지브롤터·바브엘만데브·도버·베링·마젤란은 추가 제외할 수 있다.
이 제외 정책은 해당 통로의 실시간 폐쇄 현황을 표시하는 것이 아니다.
경로가 없거나 원천을 검사할 수 없으면 오류를 내며 직선 대체를 하지 않는다.

가장 가까운 사용 가능 정점을 WGS84 거리로 찾되 100 nm보다 먼 접속은 거부한다.
항구 대표점↔접속점 간격은 `reference_gap_not_navigated`이며 거리·시간·연료에서 제외한다.
대표점이 반도 어느 쪽에 있는지와 실제 입항 가능한 방향을 확인한 것이 아니다.
총 경로는 최대 25,000 nm, 원형 좌표는 최대 5,000개다.
기존 수동 항로는 20점 한도를 유지하며 자동 경로의 형상을 그 한도에 맞춰 줄이지 않는다.

## 계획과 항로 분석

`POST /v1/routing/plan`에 카탈로그 항구 ID 두 개와 `avoid_passages`를 전달한다.
좌표 누락·원천 불일치 항구는 거부한다. 결과는 `data/routing/plans/<sha>.json`에 저장한다.
SHA에는 생성 시각을 포함한 전체 계획 내용이 들어가므로 조건이 같아도 재계산 ID는 달라질 수 있다.

전 지구 `POST /v1/voyage/analyze`에 `route_plan_id`와 저장된 전체 좌표를 전달하면
좌표·파일 해시·정책·해안선·완성 경로를 재검사하고 `voyage-1.4` 결과에 계획을 포함한다.
입출항 구간이 빠진 외해 구간이므로 시작 시각과 ETA도 외해 구간 기준이다.
항만 도착 마감 판정은 적용하지 않는다. 예보 지평 이후 자료는 결측으로 유지한다.

## 재현과 확인

프로젝트 루트의 가상환경 Python을 사용한다.

```sh
.venv/bin/python data/static/routing/build_network.py --offline
.venv/bin/python -m pytest -q tests/unit/test_routing_review.py
node --experimental-default-type=module --test tests/web/routing*.test.mjs
```

원천·매니페스트의 동일 바이트 재현과 변조 거부를 먼저 확인한다.
그다음 얇은 섬·날짜변경선·고위도·통로 제외·끊긴 그래프·접속점 간격·계획 좌표 변경을 확인한다.
화면에서는 부산–싱가포르, Rotterdam–Shanghai로 항구 검색부터 자동 경로와 예보 분석을 확인한다.
독립 선박 항적·공식 수로 대조는 아직 없으며 소프트웨어 회귀 통과를 항해 정확도로 보고하지 않는다.
