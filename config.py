import os
import yaml
import logging
from dotenv import load_dotenv

def load_config(mode='development'):
    """환경 설정 로드"""
    try:
        # 기본 환경변수 먼저 로드
        load_dotenv()
        
        # 모드별 환경변수 로드 (덮어쓰기)
        if mode == 'production':
            if os.path.exists('.env.production'):
                load_dotenv('.env.production', override=True)
                logging.info("프로덕션 환경 설정을 로드했습니다.")
            else:
                logging.warning(".env.production 파일을 찾을 수 없습니다. 기본 설정을 사용합니다.")
        elif mode == 'development':
            if os.path.exists('.env.development'):
                load_dotenv('.env.development', override=True)
                logging.info("개발 환경 설정을 로드했습니다.")
        
        # config.yaml 파일 로드
        config_file = 'config.yaml'
        if not os.path.exists(config_file):
            raise FileNotFoundError(f"{config_file} 파일을 찾을 수 없습니다.")
            
        with open(config_file, 'r', encoding='utf-8') as f:
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
        
        # 계좌번호 분리 및 검증
        acc_parts = config['account_no'].split('-')
        if len(acc_parts) != 2 or not acc_parts[0].isdigit() or not acc_parts[1].isdigit():
            raise ValueError("계좌번호 형식이 올바르지 않습니다. (예: 12345678-01)")
        
        config['cano'] = acc_parts[0]
        config['acnt_prdt_cd'] = acc_parts[1]
        config['mode'] = mode
        
        # 설정 값 검증
        if 'api' not in config:
            raise ValueError("config.yaml에 'api' 섹션이 없습니다.")
        
        required_config_keys = ['base_url', 'websocket_url']
        for key in required_config_keys:
            if key not in config['api']:
                raise ValueError(f"config.yaml의 api 섹션에 '{key}'가 없습니다.")
        
        logging.info(f"설정 파일이 성공적으로 로드되었습니다 ({mode} 모드).")
        return config
        
    except FileNotFoundError as e:
        logging.error(f"파일을 찾을 수 없습니다: {e}")
        raise
    except yaml.YAMLError as e:
        logging.error(f"YAML 파일 파싱 오류: {e}")
        raise
    except ValueError as e:
        logging.error(f"설정 검증 오류: {e}")
        raise
    except Exception as e:
        logging.error(f"설정 로드 중 예상치 못한 오류: {e}")
        raise
