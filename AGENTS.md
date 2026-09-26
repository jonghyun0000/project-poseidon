# Project Poseidon — 작업 규칙

이 파일이 이 저장소의 **유일한 규칙 원본**이다. 사람이든 에이전트든 여기서 시작한다.
`CLAUDE.md`는 이 파일을 불러오기만 한다. 규칙을 고칠 때는 여기만 고친다 —
같은 규칙을 두 파일에 두면 어긋날 때 어느 쪽이 참인지 알 수 없다.

**처음 왔다면 이 순서로 읽는다**: `HANDOFF.md`(지금 상태·다음 할 일) → 이 파일 → `docs/LESSONS.md`(반복된 실수)

## 0. 이 프로젝트

해양 파랑·조석·해일 예측 디지털 트윈. 1인 개발. 등록·계정 없이 공개된 데이터만 사용한다.

**최종 용도는 해운(상업 선박) 항로 시뮬레이션이다** (2026-08-23 확정).
우선순위 판단의 유일한 기준은 "해운 항로 최적화에 쓸 수 있는가"다.
레저·교육 용도로 충분한 수준은 여기서 기준이 되지 않는다.

## 1. 절대 규칙

1. 물리 방정식은 동료심사 문헌에서만 가져온다. 추측 금지. 근거 문헌을 코드 주석과 문서에 남긴다.
2. 모든 엔진은 해석해 검증을 통과하기 전에 운영에 쓰지 않는다.
3. AI는 물리를 대체하지 않고 보정만 한다. 보정 여부는 API 응답에 항상 노출한다.
4. Phase 게이트를 통과하지 않고 다음 단계로 넘어가지 않는다.
5. **성과를 부풀리지 않는다.** 검증 불가 조건이면 "검증 불가"라고 쓴다. 기각된 가설과 실패도 문서에 남긴다. 이 프로젝트의 문서는 지금까지 그렇게 쓰여 있다 — 그 관행을 깨지 말 것.

## 2. 환경

- 루트: `/Volumes/T7/클로드 코드 T7/클로드 대형 프로젝트/해양 파도 예측 프로그램 클로드` — **T7 외장 SSD 연결 필수**
  - **다른 경로나 다른 기계로 옮겼다면 `HANDOFF.md` §4를 먼저 한다.** 카탈로그가 데이터 경로를 절대경로로 저장한다
- Python은 반드시 `.venv/bin/python` (3.13.7). 시스템 python 금지
- `poseidon` 패키지는 site-packages에 설치돼 있지 않다. **반드시 프로젝트 루트에서 실행**해야 import된다
- **실행은 `.venv/bin/python -m <모듈>` 형태로 한다.** `.venv/bin/uvicorn` 같은 콘솔 스크립트보다 경로 이동에 강하다
- `data`, `.venv`는 `*.nosync`로 가는 **상대** 심볼릭 링크(iCloud 동기화 제외용, PHASE3 §12). 링크를 지우거나 실체 디렉토리로 바꾸지 말 것. 폴더째 복사하면 링크도 살아 있다
- 의존성: 범위는 `pyproject.toml`, 정확한 버전은 `requirements.lock.txt`(58개 고정, 2026-09-11). 둘이 어긋나면 잠금 파일이 실측이다
- pip 가 "Ignoring invalid distribution -xxx" 경고를 수십 줄 내는 것은 **무해하다**. T7(exFAT)이 모든 파일 옆에 만드는 `._*` AppleDouble 파일을 pip가 배포판으로 오인한 것이다
- ruff·mypy는 **미설치** 상태다. 필요하면 `.venv/bin/pip install ruff mypy` 후 쓴다. 설정은 pyproject.toml에 이미 있다(line-length 100, mypy strict, ruff TID로 모듈 경계 강제)
- 테스트: `.venv/bin/pytest -q`. 마커는 `unit` / `scientific` / `integration` / `benchmark` / `regression`
- **`scientific` 마커가 마일스톤 게이트다.** 이걸 깨는 변경은 머지하지 않는다. 약 3분 걸린다
- API 키는 환경변수로만(`POSEIDON_KMA_AUTHKEY`, `POSEIDON_KHOA_KEY`). 저장소에 절대 커밋 금지. 템플릿은 `.env.example`
  - httpx 요청 로그의 키는 `poseidon/core/log_redact.py`가 가린다. 새 키 기반 어댑터를 만들면 거기서 `install()`을 부를 것
- **git 으로 관리한다(2026-09-27 도입, 기준선 `97784c0`).** 원격 저장소는 없다 — 로컬 이력만이다. `backups/`·`web/_backup/` 은 도입 이전의 수동 사본이며 추적하지 않는다. `.github/workflows/ci.yml` 은 여전히 돈 적이 없다

## 3. 지형

```
poseidon/
  core/       types·config·catalog(SQLite: dataset/forecast_run/cycle_event/error_sample)·log_redact
  ingest/     adapters/{gfs,gfswave,ndbc,ioc,kma,khoa} + qc/checks
  datalake/   store (Zarr 격자 / Parquet 관측)
  physics/    constants·waves·tides(조화분석)·sea_surface(선형 중첩 해면)·ship_response(IMO 1228 판정)
  engines/
    shallow_water/  solver·ops·analytic  (SWE — 해일·조석)
    spectral_wave/  regional(2D 구면)·sources·grid·fetch1d·jax_kernel
  assimilation/  OI 관측 동화 (PHASE10, 기본값 off)
  scheduler/  ingest_cycle · wave_cycle(L1) · nested_cycle(L2) · surge_cycle · operational(무인 루프)
  validation/ collocate (예보x관측 -> error_sample)
  ai/         correction/linear(ResidualCorrector) · training/train_corrector
  api/        app.py (FastAPI, 라우트 18개)
  alerts/ twin/   빈 껍데기 — 계획만 있음
web/public/index.html   대시보드 (1,810줄). PHASE18에서 재설계 — 답 줄·자격 줄·시간표·지도·계보 줄·시간축·범례 + 서랍 1개
web/public/bridge.html  1인칭 선교 시점 (WebGL2, 569줄)
web/src/                비어 있음 — 정식 프론트엔드 미구현
web/_backup/            PHASE18 착수 전 대시보드 원본 사본
docs/PHASE1~18_*.md     전 산출물. OPERATIONS.md(운영), ENGINE_FREEZE.md(동결), LESSONS.md(교훈),
                        작업블록.md(요청 양식), adr/ADR-001~006, api/openapi.yaml, db/schema.sql
tests/{unit,scientific,integration}
scripts/                sample_chain·nested_chain·hindcast_chain·cycle72·rebuild_all(.sh), backfill_kma,
                        rebase_catalog(폴더 이동 후), ui_baseline.sh·ui_check.js(화면 계측)
```

각 모듈 디렉토리에 README.md가 있다. 그 모듈을 건드리기 전에 읽을 것.

## 4. 실행

```bash
cd "/Volumes/T7/클로드 코드 T7/클로드 대형 프로젝트/해양 파도 예측 프로그램 클로드"

.venv/bin/python -m uvicorn poseidon.api.app:app --port 8811        # 대시보드 http://localhost:8811/
.venv/bin/python -m poseidon.scheduler.wave_cycle --cycle <C> --hours 24    # L1 0.25°, 약 4분
.venv/bin/python -m poseidon.scheduler.nested_cycle --cycle <C> --hours 24  # L2 0.05°, 약 20분
.venv/bin/python -m poseidon.validation.collocate --cycle <C> [--level L2]
.venv/bin/python -m poseidon.scheduler.ingest_cycle --once --steps 24 --cycle <C>   # 네트워크 필요
./scripts/sample_chain.sh <C>     # 수집 -> L1 -> 콜로케이션
./scripts/nested_chain.sh <C>     # L2 -> 콜로케이션 L2

# 운영 (launchd 서비스 3개: api · operational · health). 상세: docs/OPERATIONS.md, PHASE27
set -a; source .env; set +a                                   # 키가 셸에 있어야 서비스에 들어간다
.venv/bin/python scripts/manage_services.py install          # 키 없으면 경고 — 채점이 멈춘다
.venv/bin/python scripts/manage_services.py status           # health.json 요약 포함
# 로그: ~/Library/Logs/Poseidon/*.service.log   상태: ~/Library/Application Support/Poseidon/health.json
```

- **`wave_cycle` / `nested_cycle`은 네트워크가 필요 없다.** 카탈로그의 forcing·boundary zarr만 읽는다. 네트워크가 필요한 건 `ingest_cycle`뿐
- `nested_cycle`은 **L2만** 기록한다. L1은 `wave_cycle`이 따로 만들어야 한다. 순서를 지킬 것
- 사이클 문자열 형식: `20260823T00`
- **`scripts/*.py` 는 `PYTHONPATH=.` 를 붙여 실행한다.** 직접 실행하면 루트가 `sys.path` 에 없어 `poseidon` import 가 실패한다
- 운영 루프는 입력을 오래된 순으로 확보하고 예보는 **최신 순**으로 만든다(PHASE27). 상시 운영은 전원 연결·덮개 열림·T7 연결이 전제다
- 브라우저에서 지도가 안 그려지면 **코드를 고치기 전에 창이 보이는지(`document.hidden`)부터** 확인한다 — `docs/LESSONS.md` G-1

## 5. 도메인·데이터 사실

- L1 예보 도메인 **120–148°E, 12.5–46°N**, 0.25°, hs 배열 (lead, 135, 113). 남쪽을 12.5°N까지 내린 이유는 무인증으로 쓸 수 있는 유일한 파랑부이 NDBC 52211(사이판) 확보
- L2 연안 도메인 **124–132°E, 32–39°N**, 0.05°
- 관측: NDBC · IOC 조위 · 기상청 API허브 약 50개소(2026-09-07 수집 로그 `stations=50`, 키 있을 때) · KHOA(키 미발급)
- 청정 표본은 `collocation:"sea-norm-v1"` 태그가 붙은 것만이다. 2026-09-11 `/v1/skill` 조회: **hs 4,014건·33개소·25사이클, tp 2,756건·18사이클, dir 2,907건·18사이클.** 표본이 늘면 바뀐다 — 인용할 때 조회 시각과 출처를 병기한다
- 운영 엔진 구성(20260906T18 zarr attrs 실측): `advection=uno2`, `gse=none`, `swell_dissipation=none`, `drag=wu1982`, `assimilation=none`, 소스항 `wind,ds,nl,bot,brk`. `docs/ENGINE_FREEZE.md`와 일치

## 6. 알려진 함정 — 반복하지 말 것

1. **검증 코드의 결함이 물리 엔진의 결함으로 오인된다.** 2026-08-22에 `collocate.py`가 육지 셀의 hs=0을 보간에 섞어 "L2가 천해 27% 개선"이라는 허구를 만들었고 그대로 보고까지 됐다. 해상도가 다른 두 격자를 비교할 때는 **마스크 정합성을 먼저 검증**한다
2. **LSO-CV는 관측소 단위 공변량의 과적합을 원리적으로 검출하지 못한다.** 26/26 fold가 knife-edge 더미를 골랐던 사례가 있다. 관측소 공변량으로 더미를 만들 때는 임계 민감도 스캔과 관측소 귀속 분석을 반드시 병행
3. **train/serve skew.** 서빙 피처는 학습과 동일 원천에서 만든다. 스텁 상수를 넣으면 보정이 예보를 악화시킨다. 피처가 결측이면 보정을 적용하지 말고 그 사실을 `model_version`에 노출
4. **릿지는 절편에 벌점하지 않는다.** 보정 대상이 계통 편차(=절편)인데 벌점하면 그걸 도로 누른다. 피처 표준화와 함께 쓴다
5. **fp32 JAX**에서 에너지 0인 해양 셀의 진단 평균량(σ̃, k̃)이 inf로 넘쳐 NaN이 전파된다. 물리 범위 클램프를 제거하지 말 것
6. **한글 경로**는 macOS가 NFD로 저장한다. 셸에 직접 타이핑하지 말고 탐색해서 변수에 담는다. zsh는 따옴표 없는 변수를 단어 분할하지 않고, `--include=*.py` 같은 인자를 글롭으로 먼저 펼친다 — 따옴표로 감쌀 것
7. ~~**예보 zarr의 변수는 `hs` 하나뿐이다.**~~ **[2026-08-25 해소, PHASE13]** 저장부가 8변수를 쓴다 — `hs`, 선형 모멘트 `m0/m1/m2/a1/b1`, 첨두량 `tp/dirp`. 주기·파향은 **저장하지 않고** `grid.derive()`로 유도한다(같은 정보를 두 벌 저장하면 어긋날 때 어느 쪽이 참인지 알 수 없다)
   - **모멘트를 보간하고 나서 유도한다. 반대 순서로 하지 말 것.** 모멘트는 E에 선형이라 육지에서 0이고 `sea_normalized`가 그대로 성립하지만, 각도를 직접 보간하면 359°와 1°의 평균이 180°가 된다
   - **방향 규약**: 엔진 내부 θ는 수학각·반시계·가는 방향, 저장·관측은 진북 0·시계·**오는 방향**. 변환은 `grid.to_compass_from()` 하나로만 한다
   - 옛 사이클 42개는 여전히 `hs`만 갖고 있다(백필하지 않기로 결정). 성적 보고에 사이클 수를 병기할 것
   - **Tm02 채점은 현재 불가**하다. KMA는 평균주기를 제공하지 않고, 도메인 내 NDBC는 2026-08-07 이후 송신 중단이다. KMA `tp`·`dir`의 정의도 문서에 없어 `[가설]` 상태다
8. **L1의 수치확산 문제** — 0.25° 1차 상향 이류의 수치확산이 보정 기준의 4.8배였다. L2가 이상한 게 아니라 L1이 이례값이다. L2 전용 상수 재튜닝은 과적합이므로 금지.
   **[2026-09-11 해소]** 한때 `[확인 필요]`로 적혀 있던 "GSE 기본값이 `tolman`으로 바뀌었다"는 **틀린 기록이었다.** `regional.py` 기본값은 `advection="uno2"`, `gse="none"`(regional.py 243–244행 docstring의 "(기본)")이고 운영 산출물 attrs도 `gse=none`이다
9. **물리 엔진을 바꾸는 작업과 표본 축적을 동시에 돌리지 않는다.** `error_sample`에는 어떤 이류 스킴으로 만든 예보인지 구분하는 태그가 없다(`features`에 `collocation` 태그만 있음). 엔진이 바뀐 뒤 생산된 예보로 콜로케이션하면 한 테이블에 두 물리가 구분 없이 섞이고, 보정기는 두 물리의 평균 편향을 학습하게 된다. 엔진 변경 작업이 진행 중이면 예보 생산 체인을 멈추거나, 최소한 `features`에 엔진 스킴을 기록한 뒤 진행할 것
10. **같은 사이클을 재실행하면 `_wave.zarr`를 덮어쓴다.** 그런데 `dataset` 테이블에는 행이 하나 더 등록되고, `api/app.py`의 `_wave_forecast`는 `rows[-1]`을 집는다. 기존 사이클을 다시 돌리기 전에 그 사이클의 `error_sample`이 무효화된다는 것을 인지할 것
11. **한 번에 두 가지 물리를 바꾸지 않는다.** 성적이 움직여도 원인을 귀속할 수 없다. 각각 따로 켜서 평가한다
12. **물리를 바꾸기 전에 다중 사이클 재현부터 확인한다.** 한 사이클이 지배한 신호를 일반 현상으로 오인한 것이 네 번이다(파향 임계, GFS-Wave 대비 초과분, 고파고 파향 열화, 태풍역 생성 과잉). 사이클 군집 부트스트랩이 1차이고, 관측소 군집만 쓰면 같은 기상을 여러 번 세서 낙관적이다
13. **폴더를 옮기면 카탈로그 경로가 어긋난다.** `dataset.uri`가 절대경로다. `scripts/rebase_catalog.py`로 점검·치환한다

그 밖의 누적 교훈(집계 방식, 효과 크기와 적용 범위, 사전 등록 게이트, 화면 검증 함정 등)은 `docs/LESSONS.md`에 있다.

## 7. 작업 방식

- 큰 작업은 단계로 쪼갠다. 한 단계마다 **구현 → 테스트 → 실패 분석 → 수정 → 재테스트**. 테스트를 통과하지 못한 단계를 넘기고 다음으로 가지 않는다
- 큰 변경 전에 계획을 먼저 보여주고 승인받는다. 승인 없이 본 구현을 시작하지 않는다
- **파일 전체를 다시 쓰지 않는다.** 변경점 중심으로 주고, 어디를 왜 바꿨는지 한 줄 요약을 붙인다
- 코드 파일은 기능별로 분리한다
- **이모지를 쓰지 않는다.** 코드·주석·문서·UI·커밋 메시지 전부
- 사실과 가설을 구분한다. 확인되지 않은 것은 `[가설]` 또는 `[확인 필요]`로 표기한다
- 수치와 데이터에는 출처와 기준 시점을 함께 쓴다
- 모르면 모른다고 한다. 추측으로 빈칸을 메우지 않는다
- 작업이 끝나면 해당 `docs/PHASE*.md`에 결과를 추가한다 — 성적·기각된 가설·남은 리스크 포함
- 요청은 `docs/작업블록.md` 양식으로 **한 번에 하나씩** 받는다. 여러 개를 동시에 받으면 어느 것도 끝까지 가지 않는다
- **두 에이전트(Claude·Astra)가 분업한다.** 누가 어느 경로를 맡는지, 작업 절차, 작업 카드는 `docs/분업.md` 에 있다. **남의 소유 경로는 고치지 않는다** — 필요하면 거기 요청 카드를 쓴다
- 사용자와의 대화는 한국어로 한다
