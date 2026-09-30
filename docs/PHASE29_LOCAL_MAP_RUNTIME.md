# Phase 29 — 로컬 지도 런타임

작성일: 2026-09-27 UTC. API 계약 변경 없이 브라우저 전달 경로를 먼저 개선했다.

## 변경

콘솔과 기존 `/legacy` 화면이 사용하던 MapLibre GL JS를 외부 jsDelivr URL에서 직접 받지 않고
프로젝트의 `web/public/console/vendor/maplibre-gl-5.7.1/`에서 제공한다. 두 화면이 같은 버전
5.7.1의 JS·CSS를 사용하므로 CDN 버전이 달라지는 문제를 없앴다. BSD 3-Clause 라이선스와
원본 배포 주소는 해당 디렉터리의 `LICENSE.txt`에 남겼다.

이 변경은 지도 런타임의 의존성을 줄인다. 배경 OSM 타일, 글꼴, 외부 지도 데이터는 여전히
네트워크 자료이므로 완전한 오프라인 지도나 해도는 아니다. 타일을 받지 못해도 좌표 입력,
항로 계산, API 분석과 저장 결과는 지도 렌더링과 독립적으로 유지되어야 한다.

## 확인

```sh
node --experimental-default-type=module --test tests/web/assets.test.mjs
node --experimental-default-type=module --test tests/web/*.mjs
```

브라우저에서 `/console?view=voyage&source=global`을 열어 로컬 번들을 200 응답으로 확인하고,
자동 항로 결과와 지도 선분이 계속 표시되는지 확인한다. 이 단계에서는 API와 수치 모델을 변경하지
않았으며, 다음 지도 단계는 OSM 타일·글꼴을 사내 타일 또는 선상 캐시로 교체하는 작업이다.
