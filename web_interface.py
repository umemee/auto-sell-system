from flask import Flask, render_template, jsonify, request
import subprocess
import os
import json

app = Flask(__name__)

@app.route('/')
def dashboard():
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>자동 매도 시스템 제어</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body { font-family: Arial; margin: 20px; background: #f0f0f0; }
            .container { max-width: 600px; margin: 0 auto; background: white; padding: 20px; border-radius: 10px; }
            button { padding: 15px 30px; margin: 10px; font-size: 18px; border: none; border-radius: 5px; cursor: pointer; }
            .start { background: #4CAF50; color: white; }
            .stop { background: #f44336; color: white; }
            .status { padding: 10px; margin: 10px 0; border-radius: 5px; }
            .running { background: #d4edda; color: #155724; }
            .stopped { background: #f8d7da; color: #721c24; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🔄 자동 매도 시스템</h1>
            
            <div id="status" class="status">상태 확인 중...</div>
            
            <button class="start" onclick="startSystem()">🚀 시스템 시작</button>
            <button class="stop" onclick="stopSystem()">⛔ 시스템 중지</button>
            
            <h3>📊 최근 로그</h3>
            <div id="logs" style="background: #f8f9fa; padding: 10px; height: 300px; overflow-y: scroll; font-family: monospace; font-size: 12px;"></div>
        </div>
        
        <script>
            function updateStatus() {
                fetch('/status')
                    .then(response => response.json())
                    .then(data => {
                        document.getElementById('status').innerHTML = 
                            `<strong>상태:</strong> ${data.status}<br>
                             <strong>업타임:</strong> ${data.uptime}<br>
                             <strong>마지막 업데이트:</strong> ${new Date().toLocaleString()}`;
                        document.getElementById('status').className = 
                            data.status === '실행중' ? 'status running' : 'status stopped';
                    });
            }
            
            function updateLogs() {
                fetch('/logs')
                    .then(response => response.json())
                    .then(data => {
                        document.getElementById('logs').innerHTML = data.logs.replace(/\\n/g, '<br>');
                    });
            }
            
            function startSystem() {
                fetch('/start', {method: 'POST'})
                    .then(response => response.json())
                    .then(data => {
                        alert(data.message);
                        updateStatus();
                    });
            }
            
            function stopSystem() {
                fetch('/stop', {method: 'POST'})
                    .then(response => response.json())
                    .then(data => {
                        alert(data.message);
                        updateStatus();
                    });
            }
            
            // 자동 업데이트
            setInterval(updateStatus, 5000);
            setInterval(updateLogs, 10000);
            updateStatus();
            updateLogs();
        </script>
    </body>
    </html>
    '''

@app.route('/status')
def get_status():
    try:
        result = subprocess.run(['systemctl', 'is-active', 'auto-sell.service'], 
                              capture_output=True, text=True)
        status = "실행중" if result.stdout.strip() == "active" else "중지됨"
        
        # 업타임 확인
        uptime_result = subprocess.run(['systemctl', 'show', 'auto-sell.service', 
                                      '--property=ActiveEnterTimestamp'], 
                                     capture_output=True, text=True)
        uptime = uptime_result.stdout.strip().split('=')[1] if '=' in uptime_result.stdout else "Unknown"
        
        return jsonify({"status": status, "uptime": uptime})
    except:
        return jsonify({"status": "오류", "uptime": "Unknown"})

@app.route('/logs')
def get_logs():
    try:
        result = subprocess.run(['tail', '-50', '/home/ubuntu/auto-sell-system/trading.log'], 
                              capture_output=True, text=True)
        return jsonify({"logs": result.stdout})
    except:
        return jsonify({"logs": "로그를 불러올 수 없습니다."})

@app.route('/start', methods=['POST'])
def start_system():
    try:
        subprocess.run(['sudo', 'systemctl', 'start', 'auto-sell.service'], check=True)
        return jsonify({"message": "✅ 시스템이 시작되었습니다!"})
    except:
        return jsonify({"message": "❌ 시스템 시작에 실패했습니다."})

@app.route('/stop', methods=['POST'])
def stop_system():
    try:
        subprocess.run(['sudo', 'systemctl', 'stop', 'auto-sell.service'], check=True)
        return jsonify({"message": "⛔ 시스템이 중지되었습니다!"})
    except:
        return jsonify({"message": "❌ 시스템 중지에 실패했습니다."})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=3000, debug=False)
