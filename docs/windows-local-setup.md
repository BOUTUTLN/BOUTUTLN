# Windows 로컬 설치 및 실행 안내서 (P0)

> 대상 PC: Windows 11, NVIDIA RTX 5060 Ti 16GB, RAM 32GB
> 모든 명령은 **Windows PowerShell** 기준입니다. 프로젝트 폴더(예: `C:\BOUTUTLN`)에서 실행하세요.
> 이 안내서의 모든 도구는 무료이며, **유료 서비스 가입이나 결제가 필요한 단계는 없습니다.**
>
> **중요:** 이 안내서와 P0 코드는 Claude Code Cloud(리눅스 개발 환경)에서 작성·테스트되었습니다. 그 환경에는 사용자 PC의 GPU·Ollama·Windows 작업 스케줄러가 없으므로, 아래 단계 중 **Ollama·GPU·작업 스케줄러 관련 항목은 아직 실제로 검증되지 않았습니다.** 사용자 PC에서 직접 확인해야 합니다.

---

## 1. Python 3.12 설치 확인

```powershell
py -3.12 --version
```

없으면 설치합니다(무료, Python 공식 배포판).

```powershell
winget install -e --id Python.Python.3.12
```

## 2. uv 설치

uv는 무료 오픈소스(MIT/Apache-2.0) 패키지·가상환경 관리 도구입니다.

```powershell
winget install -e --id astral-sh.uv
```

설치 후 새 PowerShell 창에서 확인합니다.

```powershell
uv --version
```

## 3. 프로젝트 받기와 가상환경 생성

```powershell
git clone https://github.com/BOUTUTLN/BOUTUTLN.git C:\BOUTUTLN
```

```powershell
cd C:\BOUTUTLN
```

```powershell
git switch feature/p0-foundation
```

```powershell
uv venv --python 3.12
```

## 4. 프로젝트 의존성 설치

```powershell
uv sync
```

설치되는 라이브러리는 모두 무료 오픈소스(MIT·BSD·Apache-2.0)입니다(목록은 `pyproject.toml`).

## 5. Ollama 설치

Ollama(MIT 라이선스)는 로컬 실행이 무료이고 무제한입니다. 계정 로그인은 **필요 없고 하지 않습니다.**

```powershell
winget install -e --id Ollama.Ollama
```

또는 https://ollama.com/download 에서 Windows 설치 파일을 받습니다.

## 6. Ollama 로컬 전용 설정 (모델 다운로드 전에 먼저)

클라우드 모델(토큰당 과금)을 막고, 한 번에 한 요청·한 모델만 쓰도록 사용자 환경변수를 설정합니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\set_ollama_env.ps1
```

설정되는 값:

| 변수 | 값 | 의미 |
|---|---|---|
| `OLLAMA_NO_CLOUD` | `1` | Ollama Cloud 모델·웹 검색 차단(Ollama 공식 설정) |
| `OLLAMA_NUM_PARALLEL` | `1` | 동시 요청 1개 |
| `OLLAMA_MAX_LOADED_MODELS` | `1` | 동시 적재 모델 1개(Qwen과 Gemma를 함께 올리지 않음) |
| `OLLAMA_CONTEXT_LENGTH` | `8192` | 기본 컨텍스트 길이 |

그다음 **작업 표시줄의 Ollama 아이콘에서 Quit → Ollama 다시 실행**해야 적용됩니다.
`OLLAMA_HOST`는 설정하지 마세요(기본값 `127.0.0.1:11434`가 로컬 전용입니다).

## 7. Qwen 3.5 9B 다운로드 (기본 모델, 약 6.6GB)

```powershell
ollama pull qwen3.5:9b-q4_K_M
```

## 8. Gemma 4 12B 다운로드 (비교 모델, 약 8.0GB)

```powershell
ollama pull gemma4:12b-it-q4_K_M
```

대체 모델(약 3.3GB)도 받습니다.

```powershell
ollama pull qwen3.5:4b-q4_K_M
```

설치 목록을 확인합니다.

```powershell
ollama list
```

## 9. GPU 사용 확인 (사용자 PC에서만 가능)

드라이버와 GPU가 보이는지 확인합니다. RTX 50 시리즈는 최신 NVIDIA 드라이버가 필요할 수 있습니다.

```powershell
nvidia-smi
```

모델을 한 번 실행한 뒤, 다른 PowerShell 창에서 바로 확인합니다.

```powershell
ollama run qwen3.5:9b-q4_K_M "한 문장으로 자기소개 해 줘"
```

```powershell
ollama ps
```

`PROCESSOR` 열이 `100% GPU`이면 GPU에서 실행 중입니다. `CPU`가 보이면 드라이버나 Ollama 버전을 확인하세요.

### 모델 해시 고정 (허용 목록)

프로그램은 **태그와 가중치 해시가 모두 일치하는 모델만** 실행합니다. 설치된 모델의 해시를 확인합니다.

```powershell
uv run boututln ai-check
```

`config\local.example.toml`을 `config\local.toml`로 복사합니다.

```powershell
Copy-Item config\local.example.toml config\local.toml
```

`config\local.toml`에 세 모델과 `ai-check`에 나온 해시 앞 12자리를 적습니다(목록은 통째로 바뀌므로 세 모델을 모두 적어야 합니다). 다시 실행해서 세 줄 모두 `[OK]`인지 확인합니다.

```powershell
uv run boututln ai-check
```

> AI는 기본으로 **꺼져 있습니다**(`ai.enabled = false`). P2 성능 측정(PLAN 12.7)을 통과한 뒤에 켭니다. AI가 꺼져 있어도 나머지 기능은 정상 동작합니다.

## 10. SQLite 초기화

```powershell
uv run boututln init-db
```

`data\db\backtest.db`(또는 `local.toml`에서 `mode = "paper"`이면 `paper.db`)가 생성됩니다. 실거래(`live`) DB는 만들 수 없습니다.

PC 환경 점검도 실행합니다. `FAIL`이 없어야 합니다.

```powershell
uv run boututln check-env
```

## 11. 테스트 실행

```powershell
uv run pytest
```

```powershell
uv run ruff check src tests
```

```powershell
uv run mypy
```

(선택) KB 포털에서 받은 공식 응답 예시 JSON(예: `IVU10140.json`)을 `tests\local_kb_samples\` 폴더에 넣으면, 공식 예시로도 파서를 검증합니다. 이 폴더는 Git에 올라가지 않습니다(KB 약관 제5조 ③ 재배포 금지).

## 12. 대시보드 실행 (로컬 전용)

```powershell
uv run boututln dashboard
```

브라우저에서 http://127.0.0.1:8765/status 를 엽니다. `/health`, `/costs`(비용이 0원이 아니거나 확인이 필요한 서비스 목록)도 있습니다.
대시보드는 `127.0.0.1`에서만 열리며, 외부 공개·포트 포워딩은 하지 마세요(KB 시세 재배포 금지).
(P0의 대시보드는 읽기 전용 상태 API입니다. Streamlit 화면은 P3 범위입니다.)

## 13. Windows 작업 스케줄러 등록

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\register_tasks.ps1 -DailyTime "07:30"
```

등록되는 작업:

| 작업 | 실행 시점 | 하는 일 |
|---|---|---|
| `BOUTUTLN-Daily` | 매일 07:30 | 중단된 작업 복구 → 하루 1회 실행 |
| `BOUTUTLN-OnLogon` | 로그온 시(재부팅 후) | 같은 절차로 복구·재개 |

두 작업 모두 같은 스크립트를 실행하며, **같은 날짜는 한 번만 완료**되므로 여러 번 실행돼도 안전합니다. 로그는 `data\logs\daily-YYYYMMDD.log`에 남습니다.

확인:

```powershell
Get-ScheduledTask -TaskName "BOUTUTLN-*"
```

해제:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\unregister_tasks.ps1
```

### PC 전원 관련 주의
- **PC가 완전히 종료(전원 꺼짐)되어 있으면 작업이 실행되지 않습니다.** "작업을 실행하기 위해 절전 모드 해제"는 **절전·최대 절전 상태에서만** 동작하며, 전원 옵션에서 "절전 모드 해제 타이머 허용"이 켜져 있어야 합니다.
- 놓친 작업은 PC를 켜고 로그온하면 실행되지만, **지난 장 시간대의 매매는 시도하지 않습니다**(PLAN 8, 14.1).
- Windows 업데이트 자동 재시작이 장 시간대와 겹치지 않도록 "사용 시간(활성 시간)"을 설정하세요.
- 자동매매 시간대에는 VPN을 끄세요(KB는 해외 IP 접속을 차단합니다).

---

## 부록: 인증정보 (지금은 필요 없음)

KB Open API 키는 P0에서 사용하지 않습니다. 이용료 확인과 사용자 승인 후(PLAN 3) 등록할 때는 Windows 자격 증명 관리자에 저장합니다. 명령을 실행하면 값을 입력하라는 프롬프트가 나오며, 입력한 값은 화면·파일·Git에 남지 않습니다.

```powershell
uv run python -m keyring set boututln kb_app_key
```

```powershell
uv run python -m keyring set boututln kb_app_secret
```

`.env` 파일이나 설정 파일에 키를 적지 마세요.
