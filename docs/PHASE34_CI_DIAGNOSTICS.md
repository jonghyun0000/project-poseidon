# Phase 34 — CI 검사 분리와 독립 체크아웃 회귀

2026-10-26. 기준 소스 `8f441dd45803f1b35b8973a518f1fb13c7fa811f`.
이 변경은 CI 전체 복구 완료가 아니다. 린트·포맷·타입 부채를 그대로 드러내면서
회귀 검사가 앞선 린트 실패 때문에 실행되지 않는 문제를 고친다.

## 확인한 실패

[GitHub 실행 40220236868](https://github.com/jonghyun0000/project-poseidon/actions/runs/40220236868)의
`ruff check .`가 567건으로 실패했다. 이후 포맷·타입·단위 검사는 실행되지 않았다.
로컬 독립 체크아웃에서도 Ruff 0.17.2로 567건을 재현했다.
주요 분류는 E501 320건, N806 74건, I001 49건, E702 39건, B905 26건이다.

추가로 실행한 `ruff format --check .`는 124개 파일의 변경을 요구했다.
이 Ruff 버전은 과거 Markdown 문서의 코드 블록도 검사하므로 과거 연구 문서까지
일괄 재작성하지 않았다. mypy 2.4.0의 strict 검사는 57개 파일에서 787건의 오류를 냈다.
타입 인수·반환 주석뿐 아니라 Optional 인덱싱, 인수 타입 및 외부 라이브러리 stub 문제도 있다.
이를 무시하거나 `continue-on-error`로 성공 처리하지 않는다.

## 변경 범위

- 기존 Ruff 린트·포맷·mypy 명령을 독립 matrix 작업으로 나누고 `fail-fast: false`로 실행한다.
- 회귀 작업은 `pytest -m "not scientific"`을 사용한다. 현재 `unit`만 선택하면 243개지만,
  표식이 없는 회귀 및 API 계약을 포함한 비scientific 검사는 387개다.
- nightly의 scientific/integration 조건과 명령은 유지한다.
- 각 CI 작업의 `POSEIDON_DATA_ROOT`는 runner 임시 경로로 분리한다.
- 디스크 여유 단위 검사는 `tmp_path`로 설정을 주입해 Git에 없는 운영 data 폴더를 요구하지 않는다.
- 조종 해안 API에 합성 사각형의 clipping/metadata 계약 검사를 추가했다.
  실제 서울·부산 검사는 원래 assertion을 유지하며 Natural Earth `.shp`가 없을 때만
  이유와 함께 skip한다. 파일이 존재하지만 잘못된 경우는 skip하지 않는다.
  합성 사각형 통과는 실제 해안 정확도 검증이 아니다.

물리 엔진·운영 스케줄러·API 구현·의존성 버전은 변경하지 않았다. 서비스를 재시작하지 않았다.

## 로컬 검증과 제한

환경: macOS arm64, Python 3.13.7, `requirements.lock.txt`, Ruff 0.17.2, mypy 2.4.0.
`.venv`는 `.venv.nosync` 상대 링크로 만들었으며 기존 운영 자료는 복사하지 않았다.
CI는 기존대로 Ubuntu/Python 3.12와 `uv sync --all-groups`를 사용하므로
아래 로컬 결과를 GitHub 실행 결과로 해석하지 않는다.

| 검사 | 결과 |
|---|---|
| 수정 전 비scientific 전체 | 373 passed, 11 skipped, 2 failed, 91 deselected |
| 수정 전 scientific 기준선 | 87 passed, 4 skipped, 386 deselected |
| 수정 후 격리된 비scientific 전체 | 375 passed, 12 skipped, 91 deselected |
| 수정 후 Ruff 전체 | 566건 남음; 수정한 해안 API 테스트 파일 단독 lint 통과 |
| YAML 및 matrix 검사 | 명령·실패 전파·nightly 조건 확인 통과 |
| `git diff --check` | 통과 |

수정 전 실패 두 건은 무시된 data 디렉토리와 Natural Earth 자료를 가정한 테스트였다.
scientific의 4개 skip은 관측 표본 미보유에 따른 것이며 검증 통과 수에 넣지 않았다.

실패한 재실행도 남긴다. scientific 검사에서 빈 `data/catalog.sqlite`가 만들어진 뒤
같은 기본 data 경로로 비scientific 검사를 다시 실행하면, 기존 API 검사들이 파일 존재를
실제 예보 자료 존재로 간주하여 7 failed, 375 passed, 5 skipped가 됐다.
새 임시 data 경로에서 위 375 passed/12 skipped를 확인했다. 운영 카탈로그를 삭제하거나
빈 자료로 성공 응답을 만들지 않았다. 기존 API 검사들의 자료 충족 조건 개선은 별도 후속이다.

실행한 회귀 명령:

```sh
POSEIDON_DATA_ROOT="$(mktemp -d /tmp/poseidon-ci-regression.XXXXXX)" \
  .venv/bin/python -m pytest -q -m 'not scientific'
.venv/bin/python -m pytest -q -m scientific
.venv/bin/python -m ruff check . --output-format=json
.venv/bin/python -m mypy --no-error-summary
```

## 남은 작업

CI는 린트·포맷·타입 작업 때문에 계속 실패할 예정이다. 해당 작업들의 성공은 이 PR의 결과가 아니다.
파일 소유 경계에 따라 린트 부채와 타입 정의를 작은 변경으로 처리해야 한다.
과학 변수 표기·동결 엔진·과거 연구 문서를 CI 색상만 바꾸기 위해 일괄 수정하지 않는다.
합성 회귀와 실제 데이터 기반 검증은 계속 분리하여 보고한다.
