# config.py - 한국투자증권 API 자동매매 시스템 환경설정 (공식 표준 완전 반영)

import os
import yaml
import logging
from dotenv import load_dotenv

def load_config(mode='development'):
    """
    환경 설정 로드 및 검증
    
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
        
        # 6단계: 계좌번호 자동 분리 및 검증 (핵심 수정 부분!)
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
        
        # 9단계: 거래소 코드 검증 (선택적)
        exchange_code = config.get('trading', {}).get('exchange_code', 'NASD')
        valid_exchanges = ['NASD', 'NYSE', 'AMEX', 'NAS']
        if exchange_code not in valid_exchanges:
            logging.warning(
                f"⚠️ 알 수 없는 거래소 코드: {exchange_code}\n"
                f"💡 유효한 코드: {', '.join(valid_exchanges)}"
            )
        
        # 10단계: 기타 설정 검증
        config['mode'] = mode
        
        # API 기본 URL 검증
        if 'api' not in config or 'base_url' not in config['api']:
            raise ValueError("❌ config.yaml에 api.base_url이 정의되지 않았습니다.")
        
        # Rate Limit 설정 기본값 적용
        if 'rate_limit' not in config:
            config['rate_limit'] = {
                'requests_per_second': 1,
                'daily_limit': 5000,
                'hourly_limit': 500
            }
            logging.info("💡 Rate Limit 기본값 적용")
        
        # 11단계: 최종 로그
        logging.info(
            f"🎉 설정 파일이 성공적으로 로드되었습니다!\n"
            f"   - 모드: {mode}\n"
            f"   - API Key: {config['api_key'][:10]}...\n"
            f"   - 계좌: {cano}-{acnt_prdt_cd}\n"
            f"   - Telegram: {'활성화' if config['telegram_bot_token'] else '비활성화'}"
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


def validate_config(config):
    """
    설정 검증 함수 (추가 검증)
    
    Parameters:
        config (dict): load_config()로 로드된 설정
        
    Returns:
        bool: 검증 성공 여부
    """
    try:
        # 필수 키 확인
        required_keys = [
            'api_key', 'api_secret', 'cano', 'acnt_prdt_cd',
            'api', 'rate_limit', 'trading'
        ]
        
        for key in required_keys:
            if key not in config:
                logging.error(f"❌ 필수 설정 누락: {key}")
                return False
        
        # API 설정 확인
        if 'base_url' not in config['api']:
            logging.error("❌ api.base_url이 누락되었습니다.")
            return False
        
        # 계좌번호 길이 확인
        if len(config['cano']) != 8:
            logging.error(f"❌ CANO 길이 오류: {len(config['cano'])}자리 (8자리 필요)")
            return False
        
        if len(config['acnt_prdt_cd']) != 2:
            logging.error(f"❌ ACNT_PRDT_CD 길이 오류: {len(config['acnt_prdt_cd'])}자리 (2자리 필요)")
            return False
        
        logging.info("✅ 설정 검증 완료")
        return True
    
    except Exception as e:
        logging.error(f"❌ 검증 중 오류: {e}")
        return False


# 모듈 직접 실행 시 테스트
if __name__ == "__main__":
    # 로깅 설정
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    print("=" * 80)
    print("한국투자증권 API 설정 테스트")
    print("=" * 80)
    
    try:
        # Production 모드로 설정 로드
        config = load_config('production')
        
        # 설정 검증
        if validate_config(config):
            print("\n✅ 모든 설정이 정상적으로 로드되었습니다!")
            print(f"\n📋 설정 요약:")
            print(f"   - API Key: {config['api_key'][:15]}...")
            print(f"   - 계좌번호: {config['cano']}-{config['acnt_prdt_cd']}")
            print(f"   - 거래소: {config.get('trading', {}).get('exchange_code', 'N/A')}")
            print(f"   - Telegram: {'활성화' if config['telegram_bot_token'] else '비활성화'}")
            print(f"   - Rate Limit: {config['rate_limit']['daily_limit']}회/일")
        else:
            print("\n❌ 설정 검증 실패!")
    
    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()
