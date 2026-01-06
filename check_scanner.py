# check_scanner.py (루트 폴더에 저장)
from infra.kis_api import KisApi

def check_now():
    kis = KisApi()
    
    # 테스트하고 싶은 종목 (지금 40% 넘었다고 생각하는 종목을 여기에 적으세요)
    test_symbols = ['VSME', 'CYCN'] 
    
    print(f"\n🔎 스캐너 눈 검사 중... (대상: {test_symbols})")
    print("="*60)
    print(f"{'Jongmok':<10} | {'Current':<10} | {'Base(Prev)':<10} | {'Open':<10} | {'Gap(%)':<10} | {'Real(%)':<10}")
    print("-" * 60)
    
    for sym in test_symbols:
        try:
            data = kis.get_current_price("NASD", sym)
            if not data:
                print(f"{sym:<10} | 데이터 수신 실패")
                continue
                
            curr = data.get('last', 0)
            base = data.get('base', 0)  # 전일 종가
            open_p = data.get('open', 0) # 당일 시가
            
            # 1. 봇이 기존에 보던 시각 (시가 대비)
            bot_view = 0.0
            if open_p > 0:
                bot_view = (curr - open_p) / open_p * 100
            
            # 2. 사용자(HTS)가 보는 시각 (전일 대비)
            human_view = 0.0
            if base > 0:
                human_view = (curr - base) / base * 100
                
            print(f"{sym:<10} | ${curr:<9} | ${base:<9} | ${open_p:<9} | {bot_view:6.2f}%    | {human_view:6.2f}% (HTS)")
            
        except Exception as e:
            print(f"{sym} 에러: {e}")

    print("="*60)
    print("👉 'Gap(%)'가 낮고 'Real(%)'가 높다면, 봇은 그동안 갭상승을 무시하고 있었습니다.")
    print("👉 수정된 market_listener.py는 오른쪽 'Real(%)'를 기준으로 잡습니다.")

if __name__ == "__main__":
    check_now()