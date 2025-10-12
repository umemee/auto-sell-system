import logging
import signal
import sys
import time
import argparse
from logging.handlers import RotatingFileHandler

from config import load_config
from auth import TokenManager
from order import place_sell_order
from websocket_client import WebSocketClient

def setup_logging(debug=False, cfg=None):
    level = logging.DEBUG if debug else logging.INFO
    fmt = '%(asctime)s - %(levelname)s - %(message)s'
    logging.basicConfig(level=level, format=fmt)
    if cfg:
        fh = RotatingFileHandler(
            filename=cfg['logging'].get('file', 'trading.log'),
            maxBytes=cfg['logging']['file_max_bytes'],
            backupCount=cfg['logging']['backup_count']
        )
        fh.setLevel(level)
        fh.setFormatter(logging.Formatter(fmt))
        logging.getLogger().addHandler(fh)

def signal_handler(signum, frame):
    logging.info("📴 종료 신호 수신, 프로그램을 종료합니다.")
    sys.exit(0)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['development','production'], default='development')
    parser.add_argument('--debug', action='store_true')
    args = parser.parse_args()

    setup_logging(args.debug)
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    logging.info("🚀 자동 매도 시스템 시작")
    config = load_config(mode=args.mode)

    token_manager = TokenManager(config)
    token = token_manager.get_access_token()
    if not token:
        logging.critical("토큰 발급 실패, 종료합니다.")
        return

    def order_cb(ticker, qty, price):
        return place_sell_order(config, token_manager, ticker, qty, price)

    ws_client = WebSocketClient(config, token_manager, order_cb)

    attempts = 0
    while attempts < config['system']['max_reconnect_attempts']:
        try:
            ws_client.connect()
        except Exception as e:
            logging.error(f"WebSocket 재연결 오류: {e}")
        attempts += 1
        if attempts < config['system']['max_reconnect_attempts']:
            delay = min(10 * (2 ** attempts), 60)
            logging.info(f"{delay}초 후 재연결 시도 ({attempts}/{config['system']['max_reconnect_attempts']})")
            time.sleep(delay)
            # 토큰은 만료 10분 전까진 자동 재사용, 강제 갱신하지 않음
        else:
            logging.critical("최대 재연결 시도 초과, 종료합니다.")
            break

    ws_client.close()
    logging.info("✅ 프로그램 종료")

if __name__ == '__main__':
    main()
