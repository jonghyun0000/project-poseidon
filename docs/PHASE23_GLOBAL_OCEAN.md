# Phase 23 — 전 세계 해역·항로 확장

2026-09-12 KST. 사용자 요청: 동해안 쪽으로 보이는 범위를 전 세계로 넓힌다.

## 기존 범위가 제한된 이유

지도 이동 제한이 원인이 아니었다. 실제 Poseidon L1 산출물은 동아시아
120–148°E, 12.5–46°N 격자이며 ETOPO 지형·검증 관측도 지역 범위를 사용했다.
범위 밖 좌표는 `point outside forecast domain`을 반환했다.
기존 자체 모델을 검증 없이 전 지구 격자로 늘리는 대신,
**NOAA 전 지구 원천과 기존 동아시아 자체 모델을 선택하는 구조**를 구현했다.

## 구현된 기능

- 기본 콘솔은 전 세계. 상단 `예보 범위`에서 전 세계/동아시아 전환.
- 전 지구 파고 지도, 65개 시각의 지점 추세와 CSV. 태평양·대서양·인도양·지중해·남반구 이동.
- 지점 위도 ±90°, 경도는 −180~360 입력 후 −180~180으로 정규화한다.
  지도는 Web Mercator의 한계로 극점까지 표시하지 않는다. 수치 API 격자는 ±90°다.
- 원양 항로 최대 25,000 nm·시나리오 60일 계산. **환경 예보는 16일**이며 이후는 null이다.
- 날짜변경선과 본초자오선의 주기 경도 보간, 지도 경로의 연속 경도, 남위/서경 표기.
- 북태평양 횡단·북대서양·인도양 해상 구간 예제. 실제 승인된 항로라는 뜻은 아니다.
- 전 세계 육지 표본 검사, 출항/감속 비교, 기존 도착 마감·선택형 연료 기준선 연동.
- 원천별 입력 저장, 원천 변경 시 이전 결과 무효화, JSON/CSV 출처·정의·결측 보존.
- 기존 `/legacy`, `/bridge`와 Poseidon 지역 물리·운영 루프는 지역 기능으로 유지한다.

## 실제 원천과 변수 정의

[NOAA NCEP 제품 목록](https://www.nco.ncep.noaa.gov/pmb/products/wave/)의
GFS-Wave global 0.25°를 NOMADS GRIB filter로 받는다. 로그인/키는 사용하지 않는다.
원자료의 **0.25° 격자**를 유지하고 0~384 h를 **6시간 간격**으로 수신한다.
획득 사이클: `20260911T06`, 유효기간 2026-09-11 06:00–09-27 06:00 UTC.
65개 파일, 변환된 로컬 캐시 약 362.5 MB. 각 시각에 원본 GRIB SHA-256와 요청/수신 시각을 기록했다.

[NOAA 변수 inventory](https://www.nco.ncep.noaa.gov/pmb/products/wave/gfswave.t12z.global.0p25.f003.grib2.shtml)와
실제 GRIB 메타데이터를 대조했다.

| GRIB | API | 의미 |
|---|---|---|
| HTSGW / swh | hs | 풍파·너울을 합한 유의파고 m |
| PERPW / perpw | primary_period | 주 파 평균주기 s |
| DIRPW / dirpw | primary_direction | 주 파향 degree true |
| UGRD/VGRD surface | wind_u / wind_v | 파랑 격자의 surface 강제 바람 m/s |

PERPW/DIRPW를 지역 모델의 Tp/첨두파향으로 바꾸어 표시하지 않는다.
전 지구 응답의 `tp`, `dirp`, `tm02`, `dirm`은 제공되지 않아 null이다.
surface 바람도 자동으로 10 m 바람이라 부르지 않는다. UI는 `해상 풍속`,
CSV는 `surface_wind_ms`를 사용하며 `wind_10m_ms`는 전 지구 결과에서 비운다.

전 지구 원천에는 Poseidon 지역 모델의 RMSE·관측소 판정·AI 보정을 적용하지 않는다.
`corrected=false`, `global_provider_not_locally_validated`와 NOAA 원천을 노출한다.

## 보간·지도·육지 검사

- 경도 격자는 0~359.75°를 순환한다. −180/180, −0.1/359.9는 같은 위치다.
- Hs는 유효한 원천 해양 셀에 가중치를 정규화한 공간 보간과 시간 선형 보간을 한다.
  0 m는 유효할 수 있으며 NaN을 0으로 채우지 않는다. 유효 공간 가중치 0.05 미만은 결측이다.
- 주기·파향은 유효한 해양 가중치가 가장 큰 셀과 가까운 예보 시각을 선택한다.
  시각 동률이면 이전 시각. 각도를 산술 평균하지 않는다.
- 해빙·육지·원천 결측은 `provider_missing`, 시각 범위 밖은 `outside_forecast_time`이다.
- 지도 래스터는 경위도 격자를 Web Mercator 위도로 재투영한다. 서/동반구 이미지로 나누어
  날짜변경선에서 이미지가 접히는 것을 피한다. 지도 픽셀 표시는 최근접 원값이다.
- [Natural Earth land v5.1.1](https://www.naturalearthdata.com/downloads/10m-physical-vectors/10m-land/)의
  전 지구 육지 다각형을 최대 1 nm 간격으로 검사한다. **1:10 million 지도 축척이며 10 m 해상도가 아니다.**
  작은 섬·수로·수심·흘수·규제·공식 해도를 대체하지 않는다.
  육지 검출 시 항로 `invalid_route`, 자동 재생·연료/마감 판정을 제외한다.

## 데이터 운영과 재현

`poseidon/ingest/global_wave.py`: 순차 요청·재시도·일시 파일 후 원자적 교체,
시각별 재개, 프로세스 잠금. 65개 시각을 모두 확보한 사이클만 `latest.json`으로 공개한다.
수신 실패 시 이전 완료 사이클을 보존한다. 상태는 `/v1/global/status`에서 확인한다.
콘솔 조회가 최대 15분 간격으로 새 사이클 확인을 요청하고 수신은 별도 스레드에서 진행한다.
이는 지역 예보 생산 체인과 독립적이다. 콘솔/API가 꺼져 있으면 이 수신 확인도 실행되지 않는다.
현재 자동 보관 삭제 정책은 없으며 사용량 증가 시 캐시 보관 기준을 정해야 한다.

초기화/수동 갱신:

```sh
.venv/bin/python -m poseidon.ingest.global_wave
```

전 지구 필드: `data/global/gfswave/<cycle>/fNNN.npz`, 원천 기록 `.json`, 완료 `manifest.json`.
지형: `data/static/natural-earth-10m/`, 원천 ZIP 해시 `source.json`.
새 API: `/v1/global/meta`, `/v1/global/point`, `/v1/global/hs.png`, `/v1/global/status`.
항로: 기존 `POST /v1/voyage/analyze`에 `source: "global"`을 보낸다.
이전 요청의 기본 `source`는 regional로 유지해 하위 호환을 보존한다.
전 지구 항로 응답은 `voyage-1.2`, 지역 응답은 기존 `voyage-1.1`이다.

재현 요청: `docs/api/voyage-global-request.example.json`.
결과: `reports/voyage-global-pacific.json`, `.csv`.
북태평양 4,397.7363 nm, 기준 18 kn → 244.3187 h, 883개 지점.
기준 항로 최대 조회 Hs 2.3169 m, 최대 조회 해상풍 12.7975 m/s,
파랑·바람 시간 커버리지 모두 100%, 육지 표본 미검출.
이 수치는 해당 예보와 입력 조건의 계산 결과이며 항로 추천 또는 정확도 성적이 아니다.

## 검증

- 전체 Python: **319 passed**, 116.08 s (기존 scientific 91건 포함).
- Node 데이터 로직: **16 passed**.
- 추가 검사: 경도 순환/극점, 해양 결측 정규화, 주기/파향 정의 분리,
  시간 범위 밖 null, 장거리 허용/날짜변경선 측지선, 육지 다각형 검사,
  Mercator 래스터, 잘못된 사이클 경로 거절, 수신 실패 시 이전 공개 사이클 유지.
- 실제 API: 대서양·인도양·태평양 지점, 파리 육지, 날짜변경선 항로,
  384시간 이후 결측, 육지 통과 항로를 확인했다.
- 브라우저: 전 지구 파고 지도, 인도양 25°S/70°E Hs 3.09 m 조회,
  북태평양 3시나리오와 날짜변경선 표시, regional/global 전환 시 입력 복원·이전 결과 무효화,
  1440×1000 및 390×844 배치, 가로 넘침 없음, 브라우저 오류 없음.
- CSV는 3시나리오 총 2,649행이며 전 지구 주기·파향·surface 바람과 원천을 보존했다.
- 동결된 파랑 엔진 변경 없음. API 서비스만 갱신. 운영 PID 90164 유지.

전 세계 **데이터 조회와 항로 환경 분석**을 구현했다. 자체 물리 모델의 전 지구 검증,
원천의 고파랑/해빙/연안 오차 검증, 실제 해류, 실선 연료/기상 감속 모델은 완료하지 않았다.

## 후속 검증

이 문서의 최초 구현 이후 Phase 24에서 실제 부이·외부 관측 대조 경로를 추가했다.
최신 방법과 성적은 [Phase 24](PHASE24_GLOBAL_OBSERVATIONAL_VALIDATION.md)를 참조한다.
이 문서에 남은 '검증 미확보'는 Phase 23 완료 당시의 상태이며, 산업용 적합성은 여전히 승인되지 않았다.
