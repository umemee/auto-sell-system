# 한국투자증권 자동 매도 시스템

매수 직후 자동으로 +3% 지정가 매도 주문을 실행하는 시스템입니다.

## 설치 및 실행

1. 의존성 설치:
pip install -r requirements.txt

2. 환경변수 설정 (.env 파일 생성):
KIS_APP_KEY=your_app_key
KIS_APP_SECRET=your_app_secret
KIS_ACCOUNT_NO=12345678-01

3. 실행:
python main.py --mode production

## 주요 기능

- 실시간 체결 알림 수신
- 자동 +3% 매도 주문
- 토큰 자동 갱신
- 자동 재연결
- 로그 로테이션

## 모니터링

- 로그 파일: `trading.log`
- 디버그 모드: `python main.py --debug`
사용 방법
모든 파일을 각각 저장하세요

.env 파일에 본인의 API 키와 계좌번호를 입력하세요

pip install -r requirements.txt로 의존성을 설치하세요

python main.py --mode production으로 실행하세요