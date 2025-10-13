import os
import yaml
import logging
from dotenv import load_dotenv

def load_config(mode='development'):
    """환경 설정 로드"""
    try:
        # 환경변수 로드
        load_dotenv()
        if mode == 'production':
            if os.path.exists('.env.production'):
                load_dotenv('.env.production', override=True)
                logging.info("프로덕션 환경 설정을 로드했습니다.")
        elif mode == 'development':
            if os.path.exists('.env.development'):
                load_dotenv('.env.development', override=True)
        
        with open('config.yaml', 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        
        # 필수 환경변수 확인
        required_env_vars = ['KIS_APP_KEY', 'KIS_APP_SECRET', 'KIS_ACCOUNT_NO']
        missing_vars = []
        for var in required_env_vars:
            value = os.getenv(var)
            if not value or value.strip() == '':
                missing_vars.append(var)
        
        if missing_vars:
            raise ValueError(f"다음 환경변수가 설정되지 않았습니다: {', '.join(missing_vars)}")
        
        # 환경변수와 설정 병합
        config['api_key'] = os.getenv('KIS_APP_KEY').strip()
        config['api_secret'] = os.getenv('KIS_APP_SECRET').strip()
        config['account_no'] = os.getenv('KIS_ACCOUNT_NO').strip()
        
        # Telegram 설정 추가 (수정된 부분)
        telegram_bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID')
        
        if telegram_bot_token and telegram_chat_id:
            # 기존 config['telegram'] 방식이 아닌 직접 config에 추가
            config['telegram_bot_token'] = telegram_bot_token.strip()
            config['telegram_chat_id'] = telegram_chat_id.strip()
            logging.info("Telegram 설정이 로드되었습니다.")
        else:
            config['telegram_bot_token'] = None
            config['telegram_chat_id'] = None
            logging.info("Telegram 설정이 없습니다. 봇 기능이 비활성화됩니다.")
        
        # 계좌번호 분리
        acc_parts = config['account_no'].split('-')
        if len(acc_parts) != 2 or not acc_parts[0].isdigit() or not acc_parts[1].isdigit():
            raise ValueError("계좌번호 형식이 올바르지 않습니다. (예: 12345678-01)")
        
        config['cano'] = acc_parts[0]
        config['acnt_prdt_cd'] = acc_parts[1]
        config['mode'] = mode
        
        logging.info(f"설정 파일이 성공적으로 로드되었습니다 ({mode} 모드).")
        return config
        
    except Exception as e:
        logging.error(f"설정 로드 중 오류: {e}")
        raise