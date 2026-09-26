# poseidon.twin

디지털 트윈 상태 저장·계층(L0/L1/L2) 블렌딩·publish. API는 twin을 통해서만 상태 조회 (PHASE2 §3.4).


현재 구현: `voyage.py`는 WGS84 측지선의 이동과 해상조건 노출을 계산하는 순수 분석 모듈이다.
기존 상태 조회가 모두 twin을 거친다는 위 설명은 설계 목표이며 현재 API의 실제 경로는 아니다.
항로는 사용자 지정, 일정 SOG 가정. Phase 21 참조.

Phase 22: `environment.py`는 동일 사이클 GFS u/v의 시공간 조회·이동 상대풍을,
`performance.py`는 제공 STW–연료 곡선의 정온·무해류 기준값과 도착 마감 비교를 계산한다.
성능표 미입력/범위 밖은 연료 null. 가상 곡선은 실선 근거로 취급하지 않는다.
해류·기상 추가 저항·실선 성능 검증·최적화는 아직 포함하지 않는다.

Phase 23: `global_forecast.py`는 전 지구 주기 경도·시각 조회 및 원천 정의 보존,
`global_coast.py`는 Natural Earth 1:10 million 육지 표본 검사를 담당한다.
`route_geometry`의 기본 지역 한도는 유지하고 전 지구 호출에서만 25,000 nm로 확장한다.

Phase 26: 자동 항로의 계산은 `poseidon.routing`에서 담당하며 이 패키지의 파랑·선박 성능 계산식은 변경하지 않는다.
API가 검증한 원래 2–5,000점 형상을 `route_geometry`로 전달하고, 해상 항로망 구간만 일정 SOG로 분석한다.
항구 대표점과 접속점 사이의 미검증 간격과 항만 도착 마감은 자동 항로 분석 범위에 포함하지 않는다.
