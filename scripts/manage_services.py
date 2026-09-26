"""macOS 사용자 서비스 설치·상태·제거. 외장 SSD 경로를 현재 폴더에서 계산한다.

2026-09-22 보강 (운영 루프가 멈춘 채 방치된 것이 세 번이었다):
- operational 은 /usr/bin/caffeinate -i -s 아래에서 돈다. 유휴 절전을 막고, 전원 연결 중에는
  시스템 절전도 막는다. **덮개를 닫거나 배터리일 때의 절전은 막지 못한다** — 상시 운영은 전원 연결 필수.
- 설치하는 셸에 POSEIDON_KMA_AUTHKEY / POSEIDON_KHOA_KEY 가 있으면 operational 서비스 환경에 넣는다.
  plist 는 ~/Library/LaunchAgents 에 있고(프로젝트 폴더 밖, 인수인계로 복사되지 않음) 권한 600 으로 쓴다.
  키가 없으면 경고한다 — 09-07~09-22 에 키 없이 돌아 23사이클이 채점 0건이었다.
- health 작업: scripts/health_check.py 를 T7 밖(~/Library/Application Support/Poseidon)에 복사해
  **내장 디스크의 프레임워크 파이썬**(venv 의 기반)으로 30분마다 실행한다. T7 이 빠져도 알려야 하고,
  launchd 아래 zsh·sqlite3 는 macOS 개인정보 보호(TCC)에 막혀 T7 을 못 읽기 때문이다
  (이미 권한을 받은 Python.app 을 쓴다). 결과는 같은 폴더의 health.json.
사용: set -a; source .env; set +a; .venv/bin/python scripts/manage_services.py install
"""
from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path
import plistlib
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
SUPPORT = Path.home() / 'Library' / 'Application Support' / 'Poseidon'
KEY_VARS = ('POSEIDON_KMA_AUTHKEY', 'POSEIDON_KHOA_KEY')
CAFFEINATE = ['/usr/bin/caffeinate', '-i', '-s']   # 유휴 절전 + (전원 연결 시) 시스템 절전 방지
HEALTH_INTERVAL_S = 1800
JOBS = {
    'api': ['-m', 'uvicorn', 'poseidon.api.app:app', '--host', '127.0.0.1', '--port', '8811'],
    'operational': ['-m', 'poseidon.scheduler.operational', '--hours', '72', '--catchup-days', '9'],
}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action', choices=['install', 'status', 'remove'])
    ap.add_argument('--service', choices=['api', 'operational', 'health', 'all'], default='all')
    args = ap.parse_args()
    target = f'gui/{os.getuid()}'
    folder = Path.home() / 'Library' / 'LaunchAgents'
    folder.mkdir(parents=True, exist_ok=True)
    logs = Path.home() / 'Library' / 'Logs' / 'Poseidon'
    logs.mkdir(parents=True, exist_ok=True)
    if args.action == 'status':
        health = SUPPORT / 'health.json'
        print(health.read_text() if health.exists() else '(health.json 없음 — health 작업 미설치)')
    for name in [*JOBS, 'health']:
        if args.service not in ('all', name):
            continue
        label = f'local.poseidon.{name}'
        path = folder / f'{label}.plist'
        if args.action == 'status':
            subprocess.run(['launchctl', 'print', f'{target}/{label}'], check=False)
            continue
        subprocess.run(['launchctl', 'bootout', f'{target}/{label}'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if args.action == 'remove':
            path.unlink(missing_ok=True)
            print(f'{name}: removed')
            continue
        if name == 'health':
            SUPPORT.mkdir(parents=True, exist_ok=True)
            script = SUPPORT / 'health_check.py'
            shutil.copy2(ROOT / 'scripts' / 'health_check.py', script)
            (SUPPORT / 'health_check.sh').unlink(missing_ok=True)   # 옛 셸 판(TCC 로 실패)
            base_python = getattr(sys, '_base_executable', None) or sys.executable
            spec = {'Label': label, 'ProgramArguments': [base_python, str(script), str(ROOT)],
                    'RunAtLoad': True, 'StartInterval': HEALTH_INTERVAL_S,
                    'ProcessType': 'Background',
                    'StandardOutPath': str(logs / 'health.service.log'),
                    'StandardErrorPath': str(logs / 'health.service.log')}
        else:
            command = JOBS[name]
            env = {'PYTHONUNBUFFERED': '1'}
            prog = [str(ROOT / '.venv/bin/python'), *command]
            if name == 'operational':
                prog = [*CAFFEINATE, *prog]
                keys = {k: os.environ[k] for k in KEY_VARS if os.environ.get(k)}
                env.update(keys)
                if 'POSEIDON_KMA_AUTHKEY' not in keys:
                    print('경고: POSEIDON_KMA_AUTHKEY 미설정 — 기상청 관측을 받지 못해 채점이 멈춘다')
            spec = {'Label': label, 'ProgramArguments': prog,
                    'WorkingDirectory': str(ROOT), 'RunAtLoad': True, 'KeepAlive': True,
                    'ThrottleInterval': 60, 'ProcessType': 'Interactive' if name == 'api' else 'Background',
                    'StandardOutPath': str(logs / f'{name}.service.log'),
                    'StandardErrorPath': str(logs / f'{name}.service.log'),
                    'EnvironmentVariables': env}
        with path.open('wb') as f:
            plistlib.dump(spec, f)
        os.chmod(path, 0o600)   # 키가 들어갈 수 있다
        # bootout은 종료 완료 전에 반환할 수 있다. 기존 서비스 제거를 짧게 기다린다.
        for attempt in range(10):
            result = subprocess.run(['launchctl', 'bootstrap', target, str(path)],
                                    capture_output=True, text=True)
            if result.returncode == 0:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError(f"{name}: launchctl bootstrap failed: {result.stderr.strip()}")
        print(f'{name}: installed')


if __name__ == '__main__':
    main()
