# 🚀 해외주식 자동매도 시스템

한국투자증권 API를 활용한 미국 주식 자동 매도 시스템 (기획서 v1.0 완전 준수)

## 📋 목차

- [시스템 개요](#시스템-개요)
- [주요 기능](#주요-기능)
- [시스템 아키텍처](#시스템-아키텍처)
- [설치 방법](#설치-방법)
- [설정 가이드](#설정-가이드)
- [사용 방법](#사용-방법)
- [텔레그램 명령어](#텔레그램-명령어)
- [문제 해결](#문제-해결)
- [보안 주의사항](#보안-주의사항)

## 🎯 시스템 개요

매수 체결 후 설정된 수익률(+3%) 도달 시 자동으로 매도하는 스마트 트레이딩 시스템

### 핵심 목표 (기획서 4.1절)

1. **빠른 매도 속도**: 체결 후 즉시 감지 및 매도
2. **API 비용 절감**: 스마트 폴링으로 불필요한 호출 최소화
3. **안정성**: 오류 없이 24시간 가동
4. **간단한 유지보수**: 설정 파일 기반 관리
5. **수익률 최대화**: 3% 목표 수익률 달성

## ✨ 주요 기능

### 1. 시간대별 동작 모드 (기획서 2.3절)

| 시간대 | ET 시간 | 한국 시간 | 동작 모드 | 주기 |
|--------|---------|-----------|-----------|------|
| 프리마켓 | 04:00-09:30 | 17:00-22:30 | REST 폴링 (적응형) | 3-10초 |
| 정규장 | 09:30-12:00 | 22:30-01:00 | WebSocket (실시간) | 즉시 |
| 수면 모드 | 12:00-04:00 | 01:00-17:00 | 모든 API 호출 중지 | - |

### 2. 자동 매도 전략 (기획서 4.1절)

- **목표 수익률**: 3.0%
- **계산 방식**: (현재가 - 매수가) / 매수가 × 100
- **매도 실행**: 지정가 주문 (즉시 체결)

### 3. Rate Limit 안전 모드 (기획서 5.1절)

- **실제 제한**: 초당 20회, 일일 5,000회
- **시스템 설정**: 초당 15회 (75% 활용)
- **시간당**: 500회 (안전 마진)
- **일일**: 4,500회 (90% 도달 시 경고)

### 4. 비상 증지 조건 (기획서 5.2절)

1. ✅ Rate Limit 90% 도달
2. ✅ 연속 10회 API 오류
3. ✅ WebSocket 실패 (정규장 3회)
4. ✅ 계좌 정보 조회 실패
5. ✅ API 응답 없음 (5분)

### 5. 텔레그램 알림 (기획서 6.1절)

- 시스템 시작/종료
- 매수 감지
- 매도 성공/실패
- Rate Limit 경고
- 오류 알림
- 일일 통계 요약

## 🏗️ 시스템 아키텍처

```
┌─────────────────────────────────────────────────────────┐
│                    AWS EC2 t3.micro                     │
│                   Seoul Region                          │
└─────────────────────────────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
┌──────────────┐  ┌──────────────────┐  ┌──────────────┐
│   Python     │  │  한국투자증권     │  │  Telegram    │
│  자동매도    │  │   Open API       │  │     Bot      │
│   시스템     │  │   (REST/WS)      │  │   (알림)     │
└──────────────┘  └──────────────────┘  └──────────────┘
```

### 파일 구조

```
overseas-stock-auto-sell/
├── main.py                  # 메인 시작점
├── websocket_client.py      # WebSocket 클라이언트
├── auth.py                  # 인증/토큰 관리
├── config.py                # 설정 로드
├── telegram_bot.py          # 텔레그램 봇
├── order.py                 # 주문 API
├── smart_order_monitor.py   # 스마트 폴링
├── web_interface.py         # 웹 모니터링 (선택)
├── config.yaml              # 시스템 설정
├── .env                     # API 키 (비공개)
├── requirements.txt         # 의존성
└── README.md                # 이 파일
```

## 💻 설치 방법

### 1. 시스템 요구사항

- **Python**: 3.8 이상
- **OS**: Linux (Ubuntu 22.04 권장), macOS, Windows
- **메모리**: 최소 1GB
- **네트워크**: 안정적인 인터넷 연결

### 2. 저장소 클론

```bash
git clone https://github.com/your-username/overseas-stock-auto-sell.git
cd overseas-stock-auto-sell
```

### 3. Python 가상환경 생성

```bash
# Python 3.8+ 확인
python3 --version

# 가상환경 생성
python3 -m venv venv

# 가상환경 활성화
# Linux/macOS:
source venv/bin/activate
# Windows:
venv\Scripts\activate
```

### 4. 의존성 설치

```bash
# 전체 설치 (웹 인터페이스 포함)
pip install -r requirements.txt

# 핵심만 설치 (웹 제외)
pip install requests websocket-client PyYAML python-dotenv pytz urllib3 certifi
```

## ⚙️ 설정 가이드

### 1. 환경 변수 설정

```bash
# .env.example을 복사
cp .env.example .env

# .env 파일 수정
nano .env
```

```bash
# .env 파일 내용
KIS_APP_KEY=your_app_key_here
KIS_APP_SECRET=your_app_secret_here
KIS_ACCOUNT_NO=12345678-01

TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

### 2. config.yaml 확인

```yaml
# config.yaml
api:
  base_url: "https://openapi.koreainvestment.com:9443"
  websocket_url: "ws://ops.koreainvestment.com:21000"

order_settings:
  target_profit_rate: 3.0  # 3% 목표

trading:
  timezone: "US/Eastern"
  exchange_code: "NASD"

rate_limit:
  requests_per_second: 20
  daily_limit: 5000
```

### 3. 설정 검증

```bash
# 설정 확인
python -c "from config import load_config; load_config('production')"

# 결과:
# ✅ 설정 파일이 성공적으로 로드되었습니다!
```

## 🚀 사용 방법

### 1. 프로덕션 모드 실행

```bash
# 시스템 시작
python main.py --mode production

# 또는 nohup으로 백그라운드 실행
nohup python main.py --mode production > output.log 2>&1 &
```

### 2. 개발 모드 실행

```bash
# 디버그 로그 활성화
python main.py --mode development
```

### 3. 웹 인터페이스 (선택)

```bash
# 별도 터미널에서 실행
python web_interface.py

# 브라우저 접속
# http://localhost:5000
```

### 4. 시스템 종료

```bash
# Ctrl+C (안전한 종료)
# 또는 텔레그램에서 /stop 명령
```

## 📱 텔레그램 명령어

| 명령어 | 설명 | 예시 |
|--------|------|------|
| `/start` | 봇 시작 및 소개 | `/start` |
| `/status` | 시스템 상태 확인 | `/status` |
| `/stats` | 실시간 거래 통계 | `/stats` |
| `/stop` | 시스템 종료 | `/stop` |
| `/help` | 도움말 보기 | `/help` |

### 자동 알림

- 🚀 시스템 시작
- ⚡ 매수 체결 감지
- 📈 매도 주문 실행
- ⚠️ Rate Limit 경고
- 🚨 오류 발생
- 📊 일일 통계 요약

## 🔧 문제 해결

### 문제 1: "환경변수가 설정되지 않았습니다"

**원인**: .env 파일이 없거나 위치가 잘못됨

**해결**:
```bash
# 1. .env 파일 위치 확인 (프로젝트 루트)
ls -la .env

# 2. 파일이 없으면 생성
cp .env.example .env

# 3. 권한 확인
chmod 600 .env
```

### 문제 2: "WebSocket 연결 실패"

**원인**: 네트워크 문제 또는 URL 오류

**해결**:
```bash
# 1. 네트워크 연결 확인
ping ops.koreainvestment.com

# 2. config.yaml의 websocket_url 확인
# 실전: ws://ops.koreainvestment.com:21000
# 모의: ws://opstest.koreainvestment.com:31000

# 3. 정규장 시간인지 확인 (ET 09:30-12:00)
```

### 문제 3: "API 호출 실패 (401 Unauthorized)"

**원인**: API 키가 잘못되었거나 만료됨

**해결**:
```bash
# 1. API 키 재확인
echo $KIS_APP_KEY
echo $KIS_APP_SECRET

# 2. 한국투자증권에서 API 키 재발급
# https://apiportal.koreainvestment.com

# 3. .env 파일 업데이트 후 재시작
```

### 문제 4: "텔레그램 알림이 안 옴"

**원인**: Chat ID가 잘못됨

**해결**:
```bash
# 1. Chat ID 재확인
# @userinfobot에게 메시지 전송

# 2. 봇과 대화 시작 확인
# 봇에게 /start 명령 전송

# 3. .env 업데이트
TELEGRAM_CHAT_ID=올바른_chat_id
```

## 🔒 보안 주의사항

### 1. API 키 관리

- ✅ .env 파일을 절대 Git에 커밋하지 마세요
- ✅ API 키는 정기적으로 갱신하세요 (최소 3개월)
- ✅ 공용 서버에서는 파일 권한 설정 (`chmod 600 .env`)

### 2. 계좌 보안

- ✅ 실제 거래 계좌만 사용하세요
- ✅ 테스트는 모의투자 계좌 사용
- ✅ 계좌번호를 로그에 남기지 않도록 주의

### 3. 서버 보안

- ✅ SSH 키 기반 인증 사용
- ✅ 방화벽 설정 (필요한 포트만 개방)
- ✅ 정기적인 보안 업데이트

```bash
# 보안 검사
pip install safety
safety check -r requirements.txt
```

## 📊 성능 요구사항 (기획서 10.2절)

- **가동률**: 99% (월 7.2시간 다운타임 허용)
- **실패**: 수동 재시작으로 충분

## 🧪 테스트

```bash
# 개발 도구 설치
pip install -r requirements-dev.txt

# 테스트 실행
pytest tests/ -v --cov=.

# 코드 품질 검사
black .
flake8 --max-line-length=120
```

## 📄 라이선스

이 프로젝트는 개인/상업적 사용을 위한 것입니다.

## 📞 문의

- **기획서**: `해외주식_자동매도_시스템_기획서_v1.0.pdf` 참조
- **API 문서**: https://apiportal.koreainvestment.com
- **이슈 보고**: GitHub Issues

## 🎯 로드맵

- [x] 기획서 v1.0 완전 준수
- [x] 시간대별 동작 모드
- [x] Rate Limit 안전 모드
- [x] 텔레그램 알림
- [x] 비상 정지 메커니즘
- [ ] 다중 종목 지원
- [ ] 백테스팅 기능
- [ ] 성능 모니터링 대시보드

---

**⚠️ 투자 경고**: 이 시스템은 자동화 도구일 뿐입니다. 모든 투자 결정과 손실에 대한 책임은 사용자에게 있습니다.