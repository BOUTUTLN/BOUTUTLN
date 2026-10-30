# P0 구현 기록

> 작성일: 2026-10-30 · 브랜치: `feature/p0-foundation` (기준 `claude/ecstatic-hypatia-k1wkfj` @ `54e581e`)
> 개발·테스트 환경: Claude Code Cloud(Linux, Python 3.12.3). **사용자 Windows PC에서는 아직 실행하지 않았습니다.**

## 1. 모듈 구조

| 모듈 | 책임 |
|---|---|
| `app` | CLI 진입점(`check-env`, `init-db`, `recover`, `daily-run`, `ai-check`, `dashboard`) |
| `core` | 설정 검증, 위험한 환경변수 감지, 비밀정보 저장소, 로그 마스킹, 시계, 거래소 캘린더, PC 환경 점검 |
| `domain` | `Money`/`Currency`/`FxRate`, `Market`/`Instrument`, 주문·모드 열거형 |
| `database` | SQLite 엔진(WAL), 스키마 v1(14개 업무 테이블 + 운영 테이블 3개), Alembic 마이그레이션, 리포지토리 |
| `providers` | Bar 모델·검증, 비용 등급 레지스트리·무료 한도, 외부 호출 허용 목록, Parquet 저장소 |
| `brokers` | 주문 게이트웨이 경계, KB **조회 전용** 클라이언트(엔드포인트·응답 봉투·모델·전송·호출 제한) |
| `paper_trading` | 모의 주문 게이트웨이(로컬 DB 기록만) |
| `risk` | 킬 스위치 상태(L1/L2/L3, 자동 청산 없음) |
| `portfolio` | KRW/USD 단일 통화 장부, 평균단가·실현/미실현 손익, 수수료·세금 테이블 |
| `ai` | 출력 JSON Schema, 모델 허용 목록·해시 검증, Ollama 클라이언트(127.0.0.1 전용), Provider(기본→대체, 비교 분리) |
| `scheduler` | 하루 1회 실행기(체크포인트·중복 실행 방지·재시작 복구), P0 일일 단계(거래일·데이터 최신성) |
| `dashboard` | 읽기 전용 로컬 상태 API(FastAPI, 127.0.0.1) |
| `strategies` | 정량 전략 인터페이스(구현은 P1) |
| `backtesting` | 자리만 확보(구현은 P1) |

## 2. 설계 대비 차이와 이유 (PLAN.md v0.5 기준)

| 항목 | PLAN | P0 구현 | 이유·영향 |
|---|---|---|---|
| 디렉터리 이름 | `data/`, `strategy/`, `agent/`, `oms/`, `ledger/` 등 | 사용자 P0 지시의 이름(`providers`, `strategies`, `ai`, `portfolio`, `paper_trading` 등) | 사용자의 최신 지시를 따름. 책임 분리는 같음 |
| 설정 파일 형식 | 예시는 YAML | **TOML**(`config/settings.toml`) | Python 표준 라이브러리(`tomllib`)로 읽을 수 있어 의존성 1개 감소. 내용은 같음 |
| 초기 모의 자금 위치 | `config/paper.yaml` 예시 | `config/settings.toml`의 `[paper]` | 설정 파일 하나로 통합. 값 변경 가능 요건은 같음 |
| `think` 기본값 | 12.3절 `false`, 사용자 지시 "고정하지 않음 + 승인 전 기본값 유지" | 설정값 `think = false`(기존 기본값) + 설정으로 변경 가능 | P2에서 false/true 비교 후 사용자 승인으로만 변경 |
| KB 실제 네트워크 전송 | — | **구현하지 않음**(`DisabledTransport`만 존재) | 사용자 승인 전 실제 API 호출 금지. P1 이후 승인 시 추가 |
| Streamlit 대시보드 | 16장 | P0는 FastAPI 상태 API만 | Streamlit 화면은 P3 범위 |

## 3. KB 응답 형식 검증 근거
KB 포털에서 받은 공식 명세 JSON의 **응답 예시**로 다음을 확인하고 파서에 반영했습니다.
- 봉투: `dataHeader`(resultCode 등) + `dataBody`
- 성공: `resultCode == "200"`이고 `o_clsf`(계좌 API) 또는 `clsfP`(시세 API)가 `"SC"`
- 숫자: 0 채움 정수, 부호가 0 채움 앞에 붙음(`-000...561`), `_pN` 필드는 소수점 포함 공백 채움(`" 209.7800"`)
- 국내 종목코드에 `A` 접두어(`A005930`)

**저장소의 테스트 데이터는 이 형식을 따르는 가상 값입니다.** 공식 예시 원본은 KB 약관 제5조 ③(재배포 금지) 때문에 올리지 않았습니다.

## 4. 아직 확인되지 않아 코드에서 보수적으로 처리한 것
| 항목 | 처리 |
|---|---|
| KB 이용료 | 비용 등급 `unknown` → 승인 파일 없이는 호출 차단 |
| KB 호출 제한 | 초당 1회(설정 최대 5회) |
| KB 오류 응답 형식 | `SC`가 아닌 모든 응답을 오류로 처리 |
| `ipAddr`/`macAddr` 값 기준 | 설정값으로 두고, 없으면 호출 거부 |
| 해외 IP 차단 시 응답 | 형식 미확인. 일반 오류로 처리(전용 감지는 P1 이후) |
| 토큰 요청 형식 | 공식 예시(봉투 형식)를 따름. 개발가이드의 평면 JSON 예시와 달라 KB 확인 필요 |
| 환율 코드 `USDKRWSMBS` 의미 | 예시에서 "원/달러"로 표시된 값을 USD/KRW로 사용. 정확한 의미는 KB 확인 필요 |
| 국내 현재가 시각 | 예시에서 시각 필드가 비어 있어 `quote_time=None`(임의로 만들지 않음) |
