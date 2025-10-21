# config.py - 한국투자증권 API 자동매매 시스템 환경설정 (기획서 v1.0 완전 준수)

import os
import yaml
import logging
from dotenv import load_dotenv

def load_config(mode='development'):
    """
    환경 설정 로드 및 검증 (기획서 v1.0 완전 준수)
    
    Parameters:
        mode (str): 'development' 또는 'production'
    
    Returns:
        dict: 전체 설정이 병합된 딕셔너리
        
    Raises:
        ValueError: 필수 환경변수 누락 또는 계좌번호 형식 오류
        FileNotFoundError: config.yaml 파일 없음
    """
    try:
        # 1단계: 기본 환경변수 로드
        load_dotenv()
        
        # 2단계: 모드별 환경변수 파일 로드 (프로덕션/개발)
        if mode == 'production':
            if os.path.exists('.env.production'):
                load_dotenv('.env.production', override=True)
                logging.info("✅ 프로덕션 환경 설정을 로드했습니다.")
            else:
                logging.warning("⚠️ .env.production 파일이 없습니다. 기본 .env 사용")
        elif mode == 'development':
            if os.path.exists('.env.development'):
                load_dotenv('.env.development', override=True)
                logging.info("✅ 개발 환경 설정을 로드했습니다.")
            else:
                logging.warning("⚠️ .env.development 파일이 없습니다. 기본 .env 사용")
        
        # 3단계: config.yaml 파일 로드
        config_file = 'config.yaml'
        if not os.path.exists(config_file):
            raise FileNotFoundError(f"❌ {config_file} 파일을 찾을 수 없습니다.")
        
        with open(config_file, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        
        if not config:
            raise ValueError("❌ config.yaml 파일이 비어있거나 올바르지 않습니다.")
        
        # 4단계: 필수 환경변수 확인
        required_env_vars = ['KIS_APP_KEY', 'KIS_APP_SECRET', 'KIS_ACCOUNT_NO']
        missing_vars = []
        
        for var in required_env_vars:
            value = os.getenv(var)
            if not value or value.strip() == '':
                missing_vars.append(var)
        
        if missing_vars:
            raise ValueError(
                f"❌ 다음 환경변수가 설정되지 않았습니다: {', '.join(missing_vars)}\n"
                f"💡 .env.production 또는 .env 파일에 다음과 같이 설정하세요:\n"
                f"   KIS_APP_KEY=your_app_key\n"
                f"   KIS_APP_SECRET=your_app_secret\n"
                f"   KIS_ACCOUNT_NO=12345678-01"
            )
        
        # 5단계: 환경변수와 설정 병합
        config['api_key'] = os.getenv('KIS_APP_KEY').strip()
        config['api_secret'] = os.getenv('KIS_APP_SECRET').strip()
        raw_account_no = os.getenv('KIS_ACCOUNT_NO').strip()
        
        # 6단계: 계좌번호 자동 분리 및 검증
        logging.info(f"🔍 계좌번호 처리 시작: {raw_account_no}")
        
        # 하이픈 포함 여부 체크 (예: 12345678-01)
        if '-' in raw_account_no:
            acc_parts = raw_account_no.split('-')
            if len(acc_parts) != 2:
                raise ValueError(
                    f"❌ 계좌번호 형식이 올바르지 않습니다: {raw_account_no}\n"
                    f"💡 올바른 형식: 12345678-01 (8자리-2자리)"
                )
            cano = acc_parts[0].strip()
            acnt_prdt_cd = acc_parts[1].strip()
        else:
            # 하이픈 없이 10자리로 입력된 경우 (예: 1234567801)
            if len(raw_account_no) == 10 and raw_account_no.isdigit():
                cano = raw_account_no[:8]
                acnt_prdt_cd = raw_account_no[8:]
                logging.info(f"💡 하이픈 없는 계좌번호 자동 분리: {cano}-{acnt_prdt_cd}")
            else:
                raise ValueError(
                    f"❌ 계좌번호 형식이 올바르지 않습니다: {raw_account_no}\n"
                    f"💡 올바른 형식:\n"
                    f"   - 하이픈 포함: 12345678-01\n"
                    f"   - 하이픈 없음: 1234567801 (10자리 숫자)"
                )
        
        # 계좌번호 유효성 검증
        if not cano.isdigit() or len(cano) != 8:
            raise ValueError(
                f"❌ 계좌번호 앞 8자리가 올바르지 않습니다: {cano}\n"
                f"💡 숫자 8자리여야 합니다."
            )
        
        if not acnt_prdt_cd.isdigit() or len(acnt_prdt_cd) != 2:
            raise ValueError(
                f"❌ 계좌상품코드(뒤 2자리)가 올바르지 않습니다: {acnt_prdt_cd}\n"
                f"💡 숫자 2자리여야 합니다 (예: 01, 02)."
            )
        
        # 7단계: 분리된 계좌번호를 config에 저장
        config['account_no'] = raw_account_no  # 원본 보존
        config['cano'] = cano                  # CANO: 계좌번호 앞 8자리
        config['acnt_prdt_cd'] = acnt_prdt_cd  # ACNT_PRDT_CD: 계좌상품코드 뒤 2자리
        
        logging.info(f"✅ 계좌번호 분리 완료: CANO={cano}, ACNT_PRDT_CD={acnt_prdt_cd}")
        
        # 8단계: Telegram 봇 설정 추가 (선택적)
        telegram_bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID')
        
        if telegram_bot_token and telegram_chat_id:
            config['telegram_bot_token'] = telegram_bot_token.strip()
            config['telegram_chat_id'] = telegram_chat_id.strip()
            logging.info("✅ Telegram 설정이 로드되었습니다.")
        else:
            config['telegram_bot_token'] = None
            config['telegram_chat_id'] = None
            logging.info("⚠️ Telegram 설정이 없습니다. 봇 기능이 비활성화됩니다.")
        
        # ✅ 9단계: 기획서 기본값 적용 (누락된 설정)
        apply_spec_defaults(config)
        
        # 10단계: 기타 설정 검증
        config['mode'] = mode
        
        # API 기본 URL 검증
        if 'api' not in config or 'base_url' not in config['api']:
            raise ValueError("❌ config.yaml에 api.base_url이 정의되지 않았습니다.")
        
        # ✅ 11단계: 기획서 준수 검증
        logging.info("🔍 기획서 v1.0 준수 여부 검증 시작...")
        if not validate_config(config):
            raise ValueError("❌ 설정 검증 실패")

        # 12단계: 최종 로그
        logging.info(
            f"🎉 설정 파일이 성공적으로 로드되었습니다!\n"
            f"   - 모드: {mode}\n"
            f"   - API Key: {config['api_key'][:10]}...\n"
            f"   - 계좌: {cano}-{acnt_prdt_cd}\n"
            f"   - Telegram: {'활성화' if config['telegram_bot_token'] else '비활성화'}\n"
            f"   - 목표 수익률: {config.get('order_settings', {}).get('target_profit_rate', 'N/A')}%\n"
            f"   - Rate Limit: {config['rate_limit']['daily_limit']}회/일"
        )
        
        return config
    
    except FileNotFoundError as e:
        logging.error(f"❌ 파일 오류: {e}")
        raise
    except ValueError as e:
        logging.error(f"❌ 설정 오류: {e}")
        raise
    except yaml.YAMLError as e:
        logging.error(f"❌ YAML 파싱 오류: {e}")
        raise
    except Exception as e:
        logging.error(f"❌ 설정 로드 중 예상치 못한 오류: {e}")
        raise


def apply_spec_defaults(config):
    """
    ✅ 추가: 기획서 v1.0 기본값 적용
    
    누락된 설정에 대해 기획서 기본값을 적용합니다.
    """
    # ✅ 1. Rate Limit 설정 (기획서 5.1절)
    if 'rate_limit' not in config:
        config['rate_limit'] = {}
    
    rate_limit_defaults = {
        'requests_per_second': 20,      # 기획서: 초당 20회
        'daily_limit': 5000,             # 기획서: 일일 5,000회
        'hourly_limit': 500,             # 안전 마진 (시간당 약 500회)
        'minute_limit': 100              # 안전 마진 (분당 약 100회)
    }
    
    for key, default_value in rate_limit_defaults.items():
        if key not in config['rate_limit']:
            config['rate_limit'][key] = default_value
            logging.info(f"💡 Rate Limit 기본값 적용: {key}={default_value}")
    
    # ✅ 2. 자동 매도 전략 (기획서 4.1절)
    if 'order_settings' not in config:
        config['order_settings'] = {}
    
    if 'target_profit_rate' not in config['order_settings']:
        config['order_settings']['target_profit_rate'] = 3.0  # 기획서: 3.0%
        logging.info("💡 목표 수익률 기본값 적용: 3.0%")
    
    # ✅ 3. 시스템 설정 (기획서 5.2절, 6.2절)
    if 'system' not in config:
        config['system'] = {}
    
    system_defaults = {
        'max_reconnect_attempts': 3,           # 기획서 5.2절: WebSocket 3회 실패 시 종지
        'base_reconnect_delay': 5,             # 재연결 지연 5초
        'token_refresh_margin_minutes': 5,     # 토큰 만료 5분 전 갱신
        'approval_validity_seconds': 43200,    # Approval Key 유효 시간 12시간
        'approval_refresh_margin_seconds': 3600  # Approval Key 갱신 1시간 전
    }
    
    for key, default_value in system_defaults.items():
        if key not in config['system']:
            config['system'][key] = default_value
            logging.info(f"💡 시스템 설정 기본값 적용: {key}={default_value}")
    
    # ✅ 4. 거래 설정 (기획서 2.1절)
    if 'trading' not in config:
        config['trading'] = {}
    
    trading_defaults = {
        'timezone': 'US/Eastern',              # 기획서: 미국 동부시간
        'exchange_code': 'NASD',               # 나스닥
        'default_symbol': 'AAPL'               # 기본 종목
    }
    
    for key, default_value in trading_defaults.items():
        if key not in config['trading']:
            config['trading'][key] = default_value
            logging.info(f"💡 거래 설정 기본값 적용: {key}={default_value}")
    
    # ✅ 5. 폴링 전략 (기획서 3.2절)
    if 'polling' not in config:
        config['polling'] = {}
    
    if 'aggressive' not in config['polling']:
        config['polling']['aggressive'] = {
            'time_ranges': [
                {'start': '04:00', 'end': '05:00'},  # 프리마켓 초반
                {'start': '05:00', 'end': '08:00'},  # 증가 시간대
                {'start': '08:00', 'end': '09:30'}   # 정규장 직전
            ]
        }
        logging.info("💡 폴링 전략 기본값 적용: aggressive")
    
    if 'interval_seconds' not in config['polling']:
        config['polling']['interval_seconds'] = {
            'high_activity': 3,    # 장 시작/종료 1시간
            'low_activity': 10     # 증간 시간대
        }
        logging.info("💡 폴링 주기 기본값 적용: interval_seconds")
    
    # ✅ 6. WebSocket 설정 (기획서 2.3절)
    if 'ws_mode' not in config['polling']:
        config['polling']['ws_mode'] = {
            'time_ranges': [
                {'start': '09:30', 'end': '12:00'}  # 정규장: WebSocket 사용
            ]
        }
        logging.info("💡 WebSocket 시간 범위 기본값 적용")
    
    # ✅ 7. 로깅 설정 (기획서 6.2절)
    if 'logging' not in config:
        config['logging'] = {
            'level': 'INFO',
            'file': {
                'max_size': 10485760,     # 10MB
                'backup_count': 5,
                'retention_days': 30
            }
        }
        logging.info("💡 로깅 설정 기본값 적용")


def validate_config(config):
    """
    설정 검증 함수 (기획서 v1.0 준수 여부 확인)
    
    Parameters:
        config (dict): load_config()로 로드된 설정
        
    Returns:
        bool: 검증 성공 여부
    """
    try:
        # ✅ 1. 필수 키 확인
        required_keys = [
            'api_key', 'api_secret', 'cano', 'acnt_prdt_cd',
            'api', 'rate_limit', 'trading', 'order_settings', 'system'
        ]
        
        for key in required_keys:
            if key not in config:
                logging.error(f"❌ 필수 설정 누락: {key}")
                return False
        
        # ✅ 2. API 설정 확인
        if 'base_url' not in config['api']:
            logging.error("❌ api.base_url이 누락되었습니다.")
            return False
        
        if 'websocket_url' not in config['api']:
            logging.warning("⚠️ api.websocket_url이 누락되었습니다. WebSocket 기능 제한됨.")
        
        # ✅ 3. 계좌번호 길이 확인
        if len(config['cano']) != 8:
            logging.error(f"❌ CANO 길이 오류: {len(config['cano'])}자리 (8자리 필요)")
            return False
        
        if len(config['acnt_prdt_cd']) != 2:
            logging.error(f"❌ ACNT_PRDT_CD 길이 오류: {len(config['acnt_prdt_cd'])}자리 (2자리 필요)")
            return False
        
        # ✅ 4. 시간대 확인 (기획서 2.1절)
        timezone = config.get('trading', {}).get('timezone')
        if timezone != 'US/Eastern':
            logging.warning(f"⚠️ 권장 시간대가 아닙니다: {timezone} (기획서 권장: US/Eastern)")
        
        # ✅ 5. 거래소 코드 검증 (기획서 2.1절)
        exchange_code = config.get('trading', {}).get('exchange_code', 'NASD')
        valid_exchanges = ['NASD', 'NYSE', 'AMEX', 'NAS']
        if exchange_code not in valid_exchanges:
            logging.warning(
                f"⚠️ 알 수 없는 거래소 코드: {exchange_code}\n"
                f"💡 유효한 코드: {', '.join(valid_exchanges)}"
            )
        
        # ✅ 6. 목표 수익률 검증 (기획서 4.1절)
        target_profit = config.get('order_settings', {}).get('target_profit_rate')
        if target_profit is None:
            logging.error("❌ order_settings.target_profit_rate가 누락되었습니다.")
            return False
        
        if target_profit < 1.0 or target_profit > 10.0:
            logging.warning(
                f"⚠️ 목표 수익률이 권장 범위를 벗어났습니다: {target_profit}%\n"
                f"💡 권장 범위: 1.0% ~ 10.0% (기획서 기본값: 3.0%)"
            )
        
        # ✅ 7. Rate Limit 검증 (기획서 5.1절)
        rate_limit = config.get('rate_limit', {})
        
        # 실제 제한 (공식)
        official_limits = {
            'requests_per_second': 20,
            'daily_limit': 5000
        }
        
        # 초당 요청 제한 확인
        rps = rate_limit.get('requests_per_second', 0)
        if rps > official_limits['requests_per_second']:
            logging.error(
                f"❌ 초당 요청 제한 초과: {rps} (최대 {official_limits['requests_per_second']})\n"
                f"기획서 5.1절 위반"
            )
            return False
        
        # 일일 요청 제한 확인
        daily = rate_limit.get('daily_limit', 0)
        if daily > official_limits['daily_limit']:
            logging.error(
                f"❌ 일일 요청 제한 초과: {daily} (최대 {official_limits['daily_limit']})\n"
                f"기획서 5.1절 위반"
            )
            return False
        
        # ✅ 8. WebSocket 재연결 횟수 검증 (기획서 5.2절)
        max_reconnect = config.get('system', {}).get('max_reconnect_attempts')
        if max_reconnect != 3:
            logging.warning(
                f"⚠️ WebSocket 재연결 횟수가 기획서와 다릅니다: {max_reconnect}\n"
                f"💡 기획서 5.2절 권장: 3회"
            )
        
        # ✅ 9. 폴링 전략 검증 (기획서 3.2절)
        if 'polling' not in config:
            logging.warning("⚠️ 폴링 전략(polling)이 설정되지 않았습니다.")
        else:
            if 'aggressive' not in config['polling']:
                logging.warning("⚠️ 프리마켓 폴링 전략(aggressive)이 설정되지 않았습니다.")
            
            if 'interval_seconds' not in config['polling']:
                logging.warning("⚠️ 폴링 주기(interval_seconds)가 설정되지 않았습니다.")
        
        # ✅ 10. 로깅 설정 검증 (기획서 6.2절)
        if 'logging' not in config:
            logging.warning("⚠️ 로깅 설정이 없습니다. 기본값 사용.")
        
        logging.info("✅ 기획서 v1.0 준수 검증 완료")
        return True
    
    except Exception as e:
        logging.error(f"❌ 검증 중 오류: {e}")
        return False


def get_config_summary(config):
    """
    ✅ 추가: 설정 요약 정보 반환
    
    Parameters:
        config (dict): 설정 딕셔너리
        
    Returns:
        str: 설정 요약 문자열
    """
    summary = f"""
📋 설정 요약 (기획서 v1.0 준수)
{'=' * 80}
🔐 인증 정보:
   - API Key: {config['api_key'][:15]}...
   - 계좌번호: {config['cano']}-{config['acnt_prdt_cd']}
   - Telegram: {'활성화' if config.get('telegram_bot_token') else '비활성화'}

🌍 거래 설정:
   - 시간대: {config.get('trading', {}).get('timezone', 'N/A')}
   - 거래소: {config.get('trading', {}).get('exchange_code', 'N/A')}
   - 기본 종목: {config.get('trading', {}).get('default_symbol', 'N/A')}

💰 매도 전략:
   - 목표 수익률: {config.get('order_settings', {}).get('target_profit_rate', 'N/A')}%

⚡ Rate Limit:
   - 초당: {config.get('rate_limit', {}).get('requests_per_second', 'N/A')}회
   - 일일: {config.get('rate_limit', {}).get('daily_limit', 'N/A')}회

🔄 시스템 설정:
   - WebSocket 재연결: {config.get('system', {}).get('max_reconnect_attempts', 'N/A')}회
   - 토큰 갱신 마진: {config.get('system', {}).get('token_refresh_margin_minutes', 'N/A')}분

📊 폴링 전략:
   - 고활동: {config.get('polling', {}).get('interval_seconds', {}).get('high_activity', 'N/A')}초
   - 저활동: {config.get('polling', {}).get('interval_seconds', {}).get('low_activity', 'N/A')}초

📝 로깅:
   - 레벨: {config.get('logging', {}).get('level', 'N/A')}
   - 백업: {config.get('logging', {}).get('file', {}).get('backup_count', 'N/A')}개
{'=' * 80}
"""
    return summary


# 모듈 직접 실행 시 테스트
if __name__ == "__main__":
    # 로깅 설정
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    print("=" * 80)
    print("한국투자증권 API 설정 테스트 (기획서 v1.0 준수)")
    print("=" * 80)
    
    try:
        # Production 모드로 설정 로드
        config = load_config('production')
        
        # 설정 검증
        if validate_config(config):
            print("\n✅ 모든 설정이 정상적으로 로드되었습니다!")
            print(get_config_summary(config))
        else:
            print("\n❌ 설정 검증 실패!")
    
    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()