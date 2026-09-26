# web

TypeScript+React+Vite. CesiumJS 지구 + deck.gl 오버레이 + WebGPU 해수면 셰이더 (ADR-005). G-1 구획(api/components/layers/pages/shaders) 채택.


현재 구현(2026-09-12): `public/console/`은 의존성 빌드 없이 제공하는 ES modules 기반 분석 콘솔이다.
`index.html`(구조), `style.css`(화면), `app.js`(API·지도·상태), `domain.js`(시각·결측·CSV),
`charts.js`(시계열/순환량)를 분리한다. FastAPI `/console-assets`가 정적 파일을 제공한다.
기존 `public/index.html`은 `/legacy`에 보존했다. 위 TypeScript/React/Cesium 설계는 아직 계획이다.
검증: `node --experimental-default-type=module --test tests/web/console.test.mjs`.


Phase 21: `voyage.js`(항로 화면/지도/재생)와 `voyage-domain.js`(입력·CSV)가 추가됐다.
API `POST /v1/voyage/analyze`. 화면은 입력을 로컬 저장하며 전체 분석 결과는 명시적 JSON 내보내기로 저장한다.
전체 화면 데이터 테스트: `node --experimental-default-type=module --test tests/web/*.mjs`.

Phase 22: `performance-domain.js`(제공 곡선 검증·명시적 가상 예제),
`performance-form.js`(선택 입력/복원), 항로별 바람·도착 마감·연료 기준값 UI.
실선 자료가 없을 때 기본 연료·CO₂를 만들지 않는다. 출처와 계산 범위는 JSON/CSV에도 포함된다.

Phase 23: 기본 `source=global`, 범위 선택으로 기존 regional 유지. `global-domain.js`는 경도 정규화,
날짜변경선 경로 표시, 남위/서경 표기, 원양 해상 구간 예제와 해역 이동을 담당한다.
글로벌 primary_period/primary_direction/surface wind는 지역 Tp/첨두파향/10 m와 분리해 저장한다.

Phase 24: `global-validation.js`는 실제 부이 검증 표·표본 지도·제외 기록·다운로드를 렌더링한다.
`global-altimeter.js`는 위성 구간 검증을 별도로 표시하며 두 원천의 요청 성공/실패를 독립 처리한다.
원천을 바꾸는 중 도착한 이전 응답을 차단한다. 무표본 조건을 0 오차로 표시하지 않는다.

Phase 25: `ports.js`/`ports-domain.js`는 세계 항만 검색·한국어 국가 필터·군집 지도·원천 상세를 제공한다.
`voyage-ports-domain.js`는 항구를 웨이포인트에 연결하며 좌표 변경·왕복·동일 좌표 시설을 구분한다.
선택 항구의 원천 좌표는 자동으로 이동하지 않는다. `view=ports`는 예보 수신 상태와 독립적으로 조회 가능하다.

Phase 26: `routing.js`/`routing-domain.js`는 항구 자동완성·선택 해협 회피·해상 항로 후보와 자료 시점을 표시한다.
`voyage.js`는 원본 경유점 전체를 잠금 상태로 분석하고 항구 접속 간격을 점선으로 구분한다.
계획 ID로 새로고침·JSON 입력을 복원하며, 좌표 변조와 이전 입력에 대한 늦은 응답을 차단한다.
CSV에 계획 ID·계산 범위·망 해시·검사 정책·양쪽 미검증 간격을 남긴다. 수동 항로는 자동 항로 해제로 복귀한다.
