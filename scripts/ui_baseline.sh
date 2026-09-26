#!/bin/sh
# 대시보드 소스 기준 baseline — 런타임 계측이 불가능할 때(창 숨김 등)에도 재현된다.
# 쓰는 법: ./scripts/ui_baseline.sh
cd "$(dirname "$0")/.." || exit 1
F=web/public/index.html
B=web/public/bridge.html
echo "index.html 줄수        : $(grep -c '' $F)"
echo "bridge.html 줄수       : $(grep -c '' $B)"
echo "서랍 섹션(.panel)     : $(grep -c 'class="panel"' $F)"
echo "고유 DOM id            : $(grep -o 'id="[a-zA-Z][a-zA-Z0-9_-]*"' $F | sort -u | wc -l | tr -d ' ')"
echo "position:absolute 규칙 : $(grep -c 'position:absolute' $F)"
printf '팔레트 밖 색 리터럴    : '
.venv/bin/python -c "
import io,re
s=io.open('$F',encoding='utf-8').read()
head=s.split('</style>')[0]
for blk in re.findall(r'(?::root|\[data-theme=[^]]*\])\s*\{[^}]*\}', head): s=s.replace(blk,'')
print(len(re.findall(r'#[0-9a-fA-F]{3,8}\b', s)))
"
echo "animation: blink/pulse : $(grep -c 'animation:\s*\(blink\|pulse\)' $F)"
printf '하드코딩 성적 리터럴   : '
grep -o '9\.3%\|3\.9 cm\|3\.2 m\|2\.3 m\|68건\|0\.994\|0\.214\|0\.190\|0\.248' $F $B | wc -l | tr -d ' '
echo "'경보/alarm' 어휘       : $(grep -oi 'alarm\|경보' $F $B | wc -l | tr -d ' ')"
echo "location.reload 호출   : $(grep -c 'location\.reload' $F)"
