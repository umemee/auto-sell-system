from infra.kis_api import KisApi
from infra.kis_auth import KisAuth
# 토큰 매니저 및 API 초기화
auth = KisAuth()
api = KisApi(auth)

# 테슬라(TSLA) 300개 캔들 요청
df = api.get_minute_candles("NASD", "TSLA", limit=300)

print(f"가져온 캔들 개수: {len(df)}")
if len(df) >= 300:
    print("✅ 테스트 성공: 120개 한계를 넘어 페이징 처리됨")
else:
    print(f"❌ 테스트 실패: {len(df)}개만 가져옴")
