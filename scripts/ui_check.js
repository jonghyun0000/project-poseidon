/* 대시보드 화면 회귀 점검 — 브라우저 콘솔에서 평가해 JSON 을 돌려준다.
 *
 * 쓰는 법: 이 파일 내용을 그대로 페이지 콘솔에 붙여 평가한다.
 *   .venv/bin/uvicorn poseidon.api.app:app --port 8811
 *   브라우저에서 http://localhost:8811/ 를 연 뒤 콘솔에 붙여넣기
 *
 * **먼저 hidden 을 본다.** 탭·패널이 숨김이면 requestAnimationFrame 이 발화하지 않아
 * MapLibre 가 첫 프레임도 그리지 못하고 style.load 조차 끝나지 않는다. 이때
 * getStyle() 은 undefined, isStyleLoaded() 는 영원히 false 이고 에러는 하나도 없다.
 * 2026-09-07 에 이 조용한 정지를 앱 버그로 오진해 네 번을 헛고쳤다.
 * hidden 이 true 면 아래 수치는 전부 무효다 — 창을 앞으로 꺼내고 다시 재라.
 */
(() => {
  const q = (s) => Array.from(document.querySelectorAll(s));
  const txt = (el) => (el.textContent || '').trim();

  // 성적 문자열이 나타나는 노드 (자격 줄 1곳이어야 한다)
  const SKILL_RE = /RMSE|실측 오차|불확실 폭|bias|치우침/;
  const skillNodes = q('body *').filter(
    (el) => SKILL_RE.test(txt(el)) && !Array.from(el.children).some((c) => SKILL_RE.test(txt(c))));

  // 화면에 남은 절대배치 떠 있는 패널
  const floating = q('body *').filter((el) => {
    const cs = getComputedStyle(el);
    return cs.position === 'absolute' && el.offsetWidth > 120 && el.offsetHeight > 40
        && cs.display !== 'none' && cs.visibility !== 'hidden'
        && !el.closest('.maplibregl-map');
  });

  // 서로 겹치는 떠 있는 패널 쌍
  let overlaps = 0;
  for (let i = 0; i < floating.length; i++)
    for (let j = i + 1; j < floating.length; j++) {
      const a = floating[i].getBoundingClientRect(), b = floating[j].getBoundingClientRect();
      if (a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom) overlaps++;
    }

  // 하드코딩된 성적·임계 리터럴 (2단계에서 0 이 되어야 한다)
  const body = document.body.innerText;
  const LITERALS = ['9.3%', '3.9 cm', '3.2 m', '2.3 m', '68건', '0.994', '0.214', '0.190', '0.248'];

  // 첫 화면 숫자 개수 (단순화 효과 측정용)
  const numbers = (body.match(/-?\d+(?:[.,]\d+)?/g) || []).length;

  return {
    hidden: document.hidden,            // true 면 아래 값 무효
    url: location.pathname + location.search,
    floating_panels: floating.length,
    floating_ids: floating.map((e) => e.id || e.className || e.tagName),
    overlapping_pairs: overlaps,
    skill_string_nodes: skillNodes.length,
    skill_string_ids: skillNodes.map((e) => e.id || e.className || e.tagName).slice(0, 12),
    hardcoded_literals: LITERALS.filter((s) => body.includes(s)),
    numbers_on_first_screen: numbers,
    mounts: ['answer', 'qualify', 'timetable', 'map', 'lineage', 'timeaxis', 'legend', 'drawer']
      .reduce((o, id) => (o[id] = !!document.getElementById(id), o), {}),
    body_scroll: document.body.scrollHeight > window.innerHeight + 2,
  };
})()
