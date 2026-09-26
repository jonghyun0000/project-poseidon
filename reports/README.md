# reports

검증 보고서 산출물 (M10). validation 파이프라인이 생성.


`voyage-example.json` / `voyage-example.csv`: Phase 21의 실제 과거 예보 항로 재현 산출물.
시뮬레이션 결과이며 독립 관측 검증 보고서가 아니다. 조건/시각/한계는 JSON과 Phase 21에 기록돼 있다.

`global-validation-ndbc.json/.csv`: Phase 24, 실제 NDBC Hs 관측과 NOAA 전 지구 예보 대조.
고유 관측과 재사용된 대조 쌍을 구분한다. 상세 원본·규약·해시는 `data/validation/global/runs/`에 있다.
`global-validation-altimeter.json/.csv`: 별도 Sentinel-3A 외해 구간 대조 산출물.
부이와 위성은 다른 공간 규모·표본 조건이므로 두 점수를 합산하지 않는다. 전 지구 산업용 승인 성적이 아니다.

Phase 25: `world-ports-catalog.json`은 실제 18,582개 항만 위치·시설의 등록 범위와 원본 해시다.
`world-ports-voyage-request.json`/`world-ports-voyage-result.json`은 부산–울산 대표점 연결 실제 API 시험이다.
육지 통과 `invalid_route` 결과이며 실제 항해용 항로 예제가 아니다.

Phase 26: `automatic-routing-checks.json`은 실제 자동 항로 API 및 좌표 변조 거부 확인 기록이다.
`route-KRPUS-SGSIN.json`, `route-KRPUS-USLGB.json`, `route-NLRTM-CNSGH.json`은 불변 계획의 사본이다.
`route-voyage-*-request.json`/`route-voyage-*-result.json`은 해당 해상 구간의 실제 NOAA 예보 분석이다.
`routing-screen-performance.json`은 동일 해안선 선분 검사 성능 기록이다. 독립 항적·항해용 적합성 검증은 아니다.
