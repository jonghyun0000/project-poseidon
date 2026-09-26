# poseidon.ports

공식 UN/LOCODE와 NGA World Port Index의 스냅샷을 검증해 로컬 항만 위치·시설 카탈로그로 제공한다.
`build.py`: 원본 취득·해시·페이지 정합·좌표 해석·보수적 병합·원자적 공개.
`catalog.py`: 무결성 확인·한국어/악센트 검색·국가 필터·GeoJSON·단일 판본 스냅샷.

실행은 프로젝트 루트의 `.venv/bin/python -m poseidon.ports.build --offline` 또는 `--download`.
새 UN 정식 판본은 코드에서 명시적으로 전환한다. 네트워크 오류나 원본 검사 실패 시 기존 목록을 유지한다.
좌표를 보간·지오코딩·해상 스냅하지 않는다. 항만 접근·수심·흘수·항행 가능성 판단은 포함하지 않는다.
전체 출처·수록 결과·한계는 `docs/PHASE25_WORLD_PORT_CATALOG.md` 참조.
