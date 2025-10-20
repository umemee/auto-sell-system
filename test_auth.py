# test_auth.py
import requests

# 여기에 실제 키를 직접 입력해서 테스트
APP_KEY = "PSNmYMlsLR02uvfMOzes7jy9LAnGUxpNvaAF"
APP_SECRET = "MNJ3tuB/wpBx6SOgzZ2+WC4JUvqN3WQe/0tG8Ala+oqgcytYEkO3zYiqvyDkQVV2U91Ib66hV/PELd65ucIRTt35AogMVFBr18nbSTDUAwSq0BnbFJITQwUFQXo8reZSI/otD2L/DLmH25yaISzB8lsvl1RIO0cYBherywuYHdg3MiP2GO4=
"

url = "https://openapi.koreainvestment.com:9443/oauth2/tokenP"
headers = {"Content-Type": "application/json"}
body = {
    "grant_type": "client_credentials",
    "appkey": APP_KEY,
    "appsecret": APP_SECRET
}

response = requests.post(url, headers=headers, json=body)
print(f"Status: {response.status_code}")
print(f"Response: {response.json()}")
