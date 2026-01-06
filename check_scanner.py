# check_scanner.py
from infra.kis_api import KisApi
from infra.kis_auth import KisAuth  # 👈 [추가] 인증 모듈 필수

def check_now():
    # [수정] 토큰 관리자(Auth)를 먼저 만들고 연결합니다.
    token_manager = KisAuth()
    kis = KisApi(token_manager)
    
    # 테스트하고 싶은 종목 (지금 40% 넘었다고 생각하는 종목)
    test_symbols = ['TSLA', 'NVDA', 'AAPL', 'PLTR', 'SOXL'] 
    
    print(f"\n🔎 스캐너 눈 검사 중... (대상: {test_symbols})")
    print("="*80)
    print(f"{'Jongmok':<10} | {'Current':<10} | {'Base(Prev)':<10} | {'Open':<10} | {'Gap(%)':<10} | {'Real(%)':<10}")
    print("-" * 80)
    
    for sym in test_symbols:
        try:
            data = kis.get_current_price("NASD", sym)
            if not data:
                print(f"{sym:<10} | 데이터 수신 실패 (장 운영 시간 확인)")
                continue
                
            curr = float(data.get('last', 0))
            base = float(data.get('base', 0))  # 전일 종가
            open_p = float(data.get('open', 0)) # 당일 시가
            
            # 1. 봇이 기존에 보던 시각 (시가 대비)
            bot_view = 0.0
            if open_p > 0:
                bot_view = (curr - open_p) / open_p * 100
            
            # 2. 사용자(HTS)가 보는 시각 (전일 대비)
            human_view = 0.0
            if base > 0:
                human_view = (curr - base) / base * 100
                
            print(f"{sym:<10} | ${curr:<9.2f} | ${base:<9.2f} | ${open_p:<9.2f} | {bot_view:6.2f}%    | {human_view:6.2f}% (HTS)")
            
        except Exception as e:
            print(f"{sym} 에러: {e}")

    print("="*80)
    print("👉 'Real(%)'가 HTS 수익률과 같다면, 이제 봇은 정상입니다.")

if __name__ == "__main__":
    check_now()