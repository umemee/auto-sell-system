# web_interface.py - 선택적 모니터링 웹 인터페이스 (읽기 전용)

"""
⚠️ 주의: 이 파일은 기획서 v1.0에 포함되지 않은 선택적 기능입니다.

기획서에서는 모든 모니터링과 제어를 텔레그램 봇을 통해 수행하도록 설계되어 있습니다.
이 웹 인터페이스는 추가적인 시각화 목적으로만 사용하며, 읽기 전용으로 제한됩니다.

보안 설정:
- localhost(127.0.0.1)에서만 접근 가능
- 읽기 전용 (시스템 제어 불가)
- 프로덕션 모드에서는 debug=False

사용 방법:
1. config.yaml에 web_interface 섹션 추가 (선택)
2. python web_interface.py 별도 실행
3. http://localhost:5000 접속
"""

from flask import Flask, render_template, jsonify, request, abort
import os
import json
import logging
from datetime import datetime
from pathlib import Path

app = Flask(__name__)
logger = logging.getLogger(__name__)

# ✅ 보안: 프로덕션 모드 설정
app.config['ENV'] = os.getenv('FLASK_ENV', 'production')
app.config['DEBUG'] = False  # 프로덕션에서는 항상 False

# ✅ 상태 파일 경로 (실제 시스템과 연동)
STATE_FILE = '/tmp/auto-sell-order-state.json'
LOG_FILE = 'trading.log'


def load_system_state():
    """
    ✅ 실제 시스템 상태 파일 로드
    
    main.py에서 주기적으로 업데이트하는 상태 파일 읽기
    """
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        else:
            logger.warning(f"상태 파일 없음: {STATE_FILE}")
            return None
    except Exception as e:
        logger.error(f"상태 파일 로드 오류: {e}")
        return None


def get_recent_logs(lines=50):
    """
    ✅ 최근 로그 파일 읽기
    
    Parameters:
        lines: 읽을 라인 수 (기본 50줄)
    
    Returns:
        list: 로그 라인 목록
    """
    try:
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, 'r', encoding='utf-8') as f:
                # 마지막 N줄 읽기
                all_lines = f.readlines()
                return all_lines[-lines:] if len(all_lines) > lines else all_lines
        else:
            logger.warning(f"로그 파일 없음: {LOG_FILE}")
            return ["로그 파일을 찾을 수 없습니다."]
    except Exception as e:
        logger.error(f"로그 파일 읽기 오류: {e}")
        return [f"로그 읽기 오류: {str(e)}"]


@app.route('/')
def dashboard():
    """
    ✅ 메인 대시보드 페이지 (실제 상태 연동)
    """
    # 실제 시스템 상태 로드
    state = load_system_state()
    
    return '''
    <!DOCTYPE html>
    <html lang="ko">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>자동매매 시스템 모니터링 (읽기 전용)</title>
        <style>
            * {
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }
            
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                padding: 20px;
            }
            
            .container {
                max-width: 1400px;
                margin: 0 auto;
            }
            
            .header {
                background: rgba(255, 255, 255, 0.95);
                padding: 30px;
                border-radius: 15px;
                margin-bottom: 30px;
                box-shadow: 0 10px 30px rgba(0,0,0,0.2);
                text-align: center;
            }
            
            .header h1 {
                color: #333;
                font-size: 2.5em;
                margin-bottom: 10px;
            }
            
            .header .subtitle {
                color: #666;
                font-size: 1.1em;
            }
            
            .warning-banner {
                background: #fff3cd;
                border: 2px solid #ffc107;
                border-radius: 10px;
                padding: 15px;
                margin-bottom: 20px;
                text-align: center;
            }
            
            .warning-banner strong {
                color: #856404;
            }
            
            .status-grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
                gap: 20px;
                margin-bottom: 30px;
            }
            
            .status-card {
                background: rgba(255, 255, 255, 0.95);
                padding: 25px;
                border-radius: 15px;
                box-shadow: 0 5px 15px rgba(0,0,0,0.1);
                transition: transform 0.3s, box-shadow 0.3s;
            }
            
            .status-card:hover {
                transform: translateY(-5px);
                box-shadow: 0 10px 25px rgba(0,0,0,0.2);
            }
            
            .status-card h3 {
                color: #333;
                margin-bottom: 15px;
                font-size: 1.3em;
                border-bottom: 2px solid #667eea;
                padding-bottom: 10px;
            }
            
            .status-indicator {
                display: inline-block;
                padding: 8px 16px;
                border-radius: 20px;
                font-weight: bold;
                font-size: 0.9em;
                margin-bottom: 15px;
            }
            
            .status-running {
                background: #28a745;
                color: white;
                animation: pulse 2s infinite;
            }
            
            .status-stopped {
                background: #dc3545;
                color: white;
            }
            
            .status-unknown {
                background: #6c757d;
                color: white;
            }
            
            @keyframes pulse {
                0%, 100% { opacity: 1; }
                50% { opacity: 0.7; }
            }
            
            .stat-item {
                margin: 10px 0;
                padding: 10px;
                background: #f8f9fa;
                border-radius: 8px;
            }
            
            .stat-label {
                color: #666;
                font-size: 0.9em;
            }
            
            .stat-value {
                color: #333;
                font-weight: bold;
                font-size: 1.2em;
            }
            
            .logs-section {
                background: rgba(30, 30, 30, 0.95);
                color: #0f0;
                padding: 25px;
                border-radius: 15px;
                box-shadow: 0 10px 30px rgba(0,0,0,0.3);
                font-family: 'Courier New', monospace;
                font-size: 0.9em;
                max-height: 500px;
                overflow-y: auto;
            }
            
            .logs-section h3 {
                color: #0f0;
                margin-bottom: 15px;
                font-size: 1.3em;
            }
            
            .log-line {
                margin: 5px 0;
                padding: 5px;
                border-left: 3px solid transparent;
            }
            
            .log-info { border-left-color: #0f0; }
            .log-warning { border-left-color: #ffc107; color: #ffc107; }
            .log-error { border-left-color: #dc3545; color: #dc3545; }
            
            .footer {
                text-align: center;
                margin-top: 30px;
                color: white;
                padding: 20px;
            }
            
            .refresh-info {
                text-align: center;
                color: white;
                padding: 10px;
                font-size: 0.9em;
            }
            
            /* 스크롤바 스타일링 */
            .logs-section::-webkit-scrollbar {
                width: 10px;
            }
            
            .logs-section::-webkit-scrollbar-track {
                background: #1e1e1e;
            }
            
            .logs-section::-webkit-scrollbar-thumb {
                background: #0f0;
                border-radius: 5px;
            }
        </style>
        <script>
            function refreshStatus() {
                fetch('/api/status')
                    .then(response => response.json())
                    .then(data => {
                        if (data.status === 'running') {
                            document.getElementById('system-status').className = 'status-indicator status-running';
                            document.getElementById('system-status').textContent = '실행중 ✓';
                        } else {
                            document.getElementById('system-status').className = 'status-indicator status-stopped';
                            document.getElementById('system-status').textContent = '중지됨';
                        }
                        
                        document.getElementById('update-time').textContent = data.timestamp;
                        
                        // 통계 업데이트
                        if (data.stats) {
                            document.getElementById('total-buys').textContent = data.stats.total_buys || 0;
                            document.getElementById('total-sells').textContent = data.stats.total_sells || 0;
                            document.getElementById('success-rate').textContent = data.stats.success_rate || '0.0';
                        }
                    })
                    .catch(error => {
                        console.error('Status refresh error:', error);
                        document.getElementById('system-status').className = 'status-indicator status-unknown';
                        document.getElementById('system-status').textContent = '상태 불명';
                    });
            }
            
            function refreshLogs() {
                fetch('/api/logs')
                    .then(response => response.json())
                    .then(data => {
                        const logsContainer = document.getElementById('logs-content');
                        if (data.logs && data.logs.length > 0) {
                            logsContainer.innerHTML = data.logs.map(line => {
                                let className = 'log-line log-info';
                                if (line.includes('WARNING')) className = 'log-line log-warning';
                                if (line.includes('ERROR')) className = 'log-line log-error';
                                return `<div class="${className}">${line}</div>`;
                            }).join('');
                            
                            // 자동 스크롤 (가장 아래로)
                            logsContainer.scrollTop = logsContainer.scrollHeight;
                        }
                    })
                    .catch(error => console.error('Logs refresh error:', error));
            }
            
            // 10초마다 상태 새로고침
            setInterval(refreshStatus, 10000);
            
            // 5초마다 로그 새로고침
            setInterval(refreshLogs, 5000);
            
            // 페이지 로드 시 초기화
            window.onload = function() {
                refreshStatus();
                refreshLogs();
            };
        </script>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🚀 자동매매 시스템 모니터링</h1>
                <p class="subtitle">한국투자증권 API 기반 | 기획서 v1.0 준수</p>
            </div>
            
            <div class="warning-banner">
                <strong>⚠️ 읽기 전용 모니터링</strong><br>
                시스템 제어는 텔레그램 봇을 사용하세요 (기획서 6.1절)
            </div>
            
            <div class="status-grid">
                <div class="status-card">
                    <h3>💹 시스템 상태</h3>
                    <div id="system-status" class="status-indicator status-unknown">상태 확인중...</div>
                    <div class="stat-item">
                        <div class="stat-label">마지막 업데이트</div>
                        <div class="stat-value" id="update-time">로딩중...</div>
                    </div>
                </div>
                
                <div class="status-card">
                    <h3>📈 거래 통계</h3>
                    <div class="stat-item">
                        <div class="stat-label">매수 감지</div>
                        <div class="stat-value"><span id="total-buys">0</span>건</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-label">매도 시도</div>
                        <div class="stat-value"><span id="total-sells">0</span>건</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-label">성공률</div>
                        <div class="stat-value"><span id="success-rate">0.0</span>%</div>
                    </div>
                </div>
                
                <div class="status-card">
                    <h3>⚙️ 시스템 설정</h3>
                    <div class="stat-item">
                        <div class="stat-label">목표 수익률</div>
                        <div class="stat-value">3.0%</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-label">거래소</div>
                        <div class="stat-value">NASDAQ</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-label">시간대</div>
                        <div class="stat-value">US/Eastern</div>
                    </div>
                </div>
                
                <div class="status-card">
                    <h3>📱 서비스 상태</h3>
                    <div class="stat-item">
                        <div class="stat-label">WebSocket</div>
                        <div class="stat-value">정규장 전용</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-label">스마트 폴링</div>
                        <div class="stat-value">장외 전용</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-label">텔레그램</div>
                        <div class="stat-value">알림 활성화</div>
                    </div>
                </div>
            </div>
            
            <div class="logs-section">
                <h3>📋 실시간 로그 (최근 50줄)</h3>
                <div id="logs-content">
                    <div class="log-line">로그를 불러오는 중...</div>
                </div>
            </div>
            
            <div class="refresh-info">
                🔄 상태: 10초마다 자동 갱신 | 로그: 5초마다 자동 갱신
            </div>
            
            <div class="footer">
                <p>© 2025 자동매매 시스템 | 기획서 v1.0 준수</p>
                <p><small>⚠️ 투자에는 위험이 따릅니다. 신중하게 결정하세요.</small></p>
                <p><small>🔒 localhost 전용 | 읽기 전용 인터페이스</small></p>
            </div>
        </div>
    </body>
    </html>
    '''


@app.route('/api/status')
def api_status():
    """
    ✅ 시스템 상태 API (실제 상태 파일 연동)
    
    Returns:
        JSON: 시스템 상태 정보
    """
    try:
        # 실제 상태 파일 로드
        state = load_system_state()
        
        if state:
            return jsonify({
                "status": state.get("status", "unknown"),
                "timestamp": state.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                "market_status": state.get("market_status", "unknown"),
                "stats": state.get("stats", {
                    "total_buys": 0,
                    "total_sells": 0,
                    "success_rate": "0.0"
                }),
                "websocket": state.get("websocket", {"connected": False}),
                "polling": state.get("polling", {"active": False})
            })
        else:
            # 상태 파일 없을 때 기본값
            return jsonify({
                "status": "unknown",
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "message": "시스템 상태 파일을 찾을 수 없습니다.",
                "stats": {
                    "total_buys": 0,
                    "total_sells": 0,
                    "success_rate": "0.0"
                }
            })
            
    except Exception as e:
        logger.error(f"API 상태 조회 오류: {e}")
        return jsonify({
            "status": "error",
            "message": str(e),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }), 500


@app.route('/api/logs')
def api_logs():
    """
    ✅ 로그 API (실제 로그 파일 읽기)
    
    Returns:
        JSON: 최근 로그 라인
    """
    try:
        logs = get_recent_logs(lines=50)
        return jsonify({
            "status": "success",
            "logs": [line.strip() for line in logs],
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })
    except Exception as e:
        logger.error(f"API 로그 조회 오류: {e}")
        return jsonify({
            "status": "error",
            "message": str(e),
            "logs": [],
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }), 500


@app.errorhandler(404)
def not_found(error):
    """404 에러 핸들러"""
    return jsonify({
        "status": "error",
        "message": "페이지를 찾을 수 없습니다.",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }), 404


@app.errorhandler(500)
def internal_error(error):
    """500 에러 핸들러"""
    return jsonify({
        "status": "error",
        "message": "서버 내부 오류가 발생했습니다.",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }), 500


def save_system_state_example():
    """
    ✅ 예시: main.py에서 이 함수를 참고하여 상태 파일 저장
    
    main.py의 메인 루프에서 주기적으로 호출:
    
    ```python
    state = {
        'status': 'running',
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'market_status': market_status,
        'stats': {
            'total_buys': total_buys,
            'total_sells': total_sells,
            'success_rate': f'{success_rate:.1f}'
        },
        'websocket': {
            'connected': ws_client.is_connected() if ws_client else False
        },
        'polling': {
            'active': smart_monitor.is_running if smart_monitor else False
        }
    }
    
    with open('/tmp/auto-sell-order-state.json', 'w') as f:
        json.dump(state, f)
    ```
    """
    pass


if __name__ == '__main__':
    # ✅ 로깅 설정
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    logger.info("=" * 80)
    logger.info("📊 자동매매 시스템 웹 모니터링 시작")
    logger.info("=" * 80)
    logger.info("⚠️  주의: 이 기능은 기획서 v1.0에 포함되지 않은 선택적 기능입니다.")
    logger.info("📋 기획서에서는 모든 모니터링/제어를 텔레그램으로 수행합니다.")
    logger.info("")
    logger.info("🔒 보안 설정:")
    logger.info("   - 접근: localhost(127.0.0.1)만 허용")
    logger.info("   - 모드: 읽기 전용")
    logger.info("   - Debug: 비활성화")
    logger.info("")
    logger.info("🌐 접속 주소: http://localhost:5000")
    logger.info("=" * 80)
    
    # ✅ 보안: localhost에서만 접근 가능
    # 0.0.0.0 대신 127.0.0.1 사용
    app.run(
        host='127.0.0.1',  # localhost만 허용
        port=5000,
        debug=False,       # 프로덕션: debug 비활성화
        use_reloader=False # 자동 재시작 비활성화
    )