# web_interface.py - 수정된 전체 코드

from flask import Flask, render_template, jsonify, request
import subprocess
import os
import json
from datetime import datetime

app = Flask(__name__)

@app.route('/')
def dashboard():
    """메인 대시보드 페이지"""
    return '''
    <!DOCTYPE html>
    <html lang="ko">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>자동매매 시스템 대시보드</title>
        <style>
            body {
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                margin: 0;
                padding: 20px;
                background-color: #f5f5f5;
            }
            .container {
                max-width: 1200px;
                margin: 0 auto;
                background: white;
                padding: 30px;
                border-radius: 10px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            }
            .header {
                text-align: center;
                margin-bottom: 30px;
                border-bottom: 2px solid #007bff;
                padding-bottom: 20px;
            }
            .header h1 {
                color: #333;
                margin: 0;
                font-size: 2.5em;
            }
            .status-grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
                gap: 20px;
                margin-bottom: 30px;
            }
            .status-card {
                background: #f8f9fa;
                padding: 20px;
                border-radius: 8px;
                border-left: 4px solid #007bff;
            }
            .status-card h3 {
                margin: 0 0 15px 0;
                color: #333;
            }
            .status-indicator {
                display: inline-block;
                padding: 5px 15px;
                border-radius: 20px;
                font-weight: bold;
                margin-bottom: 10px;
            }
            .status-running {
                background-color: #28a745;
                color: white;
            }
            .status-stopped {
                background-color: #dc3545;
                color: white;
            }
            .controls {
                text-align: center;
                margin: 30px 0;
            }
            .btn {
                display: inline-block;
                padding: 12px 24px;
                margin: 0 10px;
                background-color: #007bff;
                color: white;
                text-decoration: none;
                border-radius: 5px;
                border: none;
                cursor: pointer;
                font-size: 16px;
                transition: background-color 0.3s;
            }
            .btn:hover {
                background-color: #0056b3;
            }
            .btn-danger {
                background-color: #dc3545;
            }
            .btn-danger:hover {
                background-color: #c82333;
            }
            .logs {
                margin-top: 30px;
                padding: 20px;
                background-color: #f8f9fa;
                border-radius: 8px;
                border-left: 4px solid #ffc107;
            }
            .footer {
                text-align: center;
                margin-top: 30px;
                padding-top: 20px;
                border-top: 1px solid #dee2e6;
                color: #6c757d;
            }
        </style>
        <script>
            function refreshStatus() {
                fetch('/status')
                    .then(response => response.json())
                    .then(data => {
                        document.getElementById('status-time').textContent = data.timestamp;
                        // 추가 상태 업데이트 로직
                    })
                    .catch(error => console.error('Status refresh error:', error));
            }
            
            // 5초마다 상태 새로고침
            setInterval(refreshStatus, 5000);
            
            // 페이지 로드 시 초기 상태 가져오기
            window.onload = refreshStatus;
        </script>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🚀 자동매매 시스템 대시보드</h1>
                <p>한국투자증권 API 기반 자동매매 시스템</p>
            </div>
            
            <div class="status-grid">
                <div class="status-card">
                    <h3>💹 시스템 상태</h3>
                    <div class="status-indicator status-running">실행중</div>
                    <p>자동매매 시스템이 정상적으로 작동하고 있습니다.</p>
                    <p><strong>마지막 업데이트:</strong> <span id="status-time">로딩중...</span></p>
                </div>
                
                <div class="status-card">
                    <h3>🔗 WebSocket 연결</h3>
                    <div class="status-indicator status-running">연결됨</div>
                    <p>실시간 체결 데이터 수신 중입니다.</p>
                </div>
                
                <div class="status-card">
                    <h3>📱 텔레그램 봇</h3>
                    <div class="status-indicator status-running">활성화</div>
                    <p>알림 서비스가 정상적으로 동작하고 있습니다.</p>
                </div>
                
                <div class="status-card">
                    <h3>💰 수익률 설정</h3>
                    <p><strong>목표 수익률:</strong> 3.0%</p>
                    <p><strong>거래소:</strong> NASDAQ</p>
                    <p><strong>주문 유형:</strong> 지정가</p>
                </div>
            </div>
            
            <div class="controls">
                <h3>🎮 시스템 제어</h3>
                <a href="/status" class="btn">📊 상태 확인</a>
                <a href="/logs" class="btn">📝 로그 보기</a>
                <button class="btn btn-danger" onclick="if(confirm('정말로 시스템을 중지하시겠습니까?')) window.location.href='/shutdown';">🛑 시스템 중지</button>
            </div>
            
            <div class="logs">
                <h3>📋 최근 활동</h3>
                <p>• 시스템 시작: 정상적으로 초기화되었습니다.</p>
                <p>• WebSocket 연결: 한국투자증권 서버에 연결되었습니다.</p>
                <p>• 텔레그램 봇: 알림 서비스가 시작되었습니다.</p>
                <p><em>실시간 로그를 보려면 "로그 보기" 버튼을 클릭하세요.</em></p>
            </div>
            
            <div class="footer">
                <p>© 2025 자동매매 시스템 | 한국투자증권 API 기반</p>
                <p><small>⚠️ 투자에는 위험이 따릅니다. 투자 결정은 신중히 하세요.</small></p>
            </div>
        </div>
    </body>
    </html>
    '''

@app.route('/status')
def status():
    """시스템 상태 API"""
    try:
        # 실제 상태 확인 로직을 여기에 구현
        status_data = {
            "status": "running",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "websocket": {
                "connected": True,
                "last_message": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            },
            "telegram": {
                "active": True,
                "last_notification": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            },
            "trading": {
                "profit_margin": "3.0%",
                "exchange": "NASDAQ",
                "order_type": "지정가"
            }
        }
        return jsonify(status_data)
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }), 500

@app.route('/logs')
def logs():
    """로그 페이지"""
    return '''
    <!DOCTYPE html>
    <html lang="ko">
    <head>
        <meta charset="UTF-8">
        <title>시스템 로그</title>
        <style>
            body { font-family: monospace; background: #1e1e1e; color: #fff; padding: 20px; }
            .log-container { background: #2d2d2d; padding: 20px; border-radius: 5px; }
            .log-header { color: #00ff00; margin-bottom: 20px; }
            .log-line { margin: 5px 0; }
            .back-btn { 
                display: inline-block; 
                padding: 10px 20px; 
                background: #007bff; 
                color: white; 
                text-decoration: none; 
                border-radius: 5px; 
                margin-bottom: 20px;
            }
        </style>
    </head>
    <body>
        <a href="/" class="back-btn">← 대시보드로 돌아가기</a>
        <div class="log-container">
            <div class="log-header">📝 시스템 로그 (실시간)</div>
            <div class="log-line">[INFO] 시스템이 정상적으로 시작되었습니다.</div>
            <div class="log-line">[INFO] WebSocket 연결이 설정되었습니다.</div>
            <div class="log-line">[INFO] 텔레그램 봇이 활성화되었습니다.</div>
            <div class="log-line">[INFO] 체결 데이터 수신 대기 중...</div>
            <p><em>실제 로그 파일을 읽어오는 기능은 추후 구현 예정입니다.</em></p>
        </div>
    </body>
    </html>
    '''

@app.route('/shutdown')
def shutdown():
    """시스템 종료 페이지 (실제 종료는 하지 않음)"""
    return '''
    <!DOCTYPE html>
    <html lang="ko">
    <head>
        <meta charset="UTF-8">
        <title>시스템 종료</title>
        <style>
            body { 
                font-family: Arial, sans-serif; 
                text-align: center; 
                padding: 50px; 
                background: #f8d7da; 
                color: #721c24;
            }
            .warning { 
                background: white; 
                padding: 30px; 
                border-radius: 10px; 
                display: inline-block;
                border: 2px solid #f5c6cb;
            }
            .btn { 
                display: inline-block; 
                padding: 10px 20px; 
                margin: 10px; 
                text-decoration: none; 
                border-radius: 5px;
            }
            .btn-primary { background: #007bff; color: white; }
            .btn-danger { background: #dc3545; color: white; }
        </style>
    </head>
    <body>
        <div class="warning">
            <h1>⚠️ 시스템 종료 확인</h1>
            <p>자동매매 시스템을 종료하면 모든 자동 거래가 중단됩니다.</p>
            <p><strong>정말로 종료하시겠습니까?</strong></p>
            <br>
            <a href="/" class="btn btn-primary">취소</a>
            <a href="#" class="btn btn-danger" onclick="alert('웹에서는 직접 종료할 수 없습니다. 터미널에서 Ctrl+C로 종료하세요.');">확인</a>
        </div>
    </body>
    </html>
    '''

if __name__ == '__main__':
    # 개발 모드에서만 실행
    app.run(host='0.0.0.0', port=5000, debug=True)