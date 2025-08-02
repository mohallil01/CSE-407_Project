from flask import Flask, render_template, jsonify, request, Response

import requests, time, hmac, hashlib, json, sqlite3, io, csv
from datetime import datetime

app = Flask(__name__)

# ------------- Tuya config -------------
ACCESS_ID     = '3gr9xqmk8sdrtajv8suf'
ACCESS_SECRET = '610bf9742c4c499faacdfded741e387d'
DEVICE_ID     = 'd72e43109413e6fd08avc2'
BASE_URL      = 'https://openapi.tuyain.com'

# ------------- DB init -------------
def init_db():
    conn = sqlite3.connect('power.db')
    conn.execute('''CREATE TABLE IF NOT EXISTS readings
                    (ts INTEGER PRIMARY KEY, voltage REAL, current REAL, power REAL)''')
    conn.commit()
    conn.close()
init_db()

# ------------- Tuya Helpers -------------
def sign(method, path, body='', token=''):
    t = str(int(time.time() * 1000))
    msg = ACCESS_ID + (token or '') + t + f"{method}\n{hashlib.sha256(body.encode()).hexdigest()}\n\n{path}"
    return t, hmac.new(ACCESS_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest().upper()

def get_token():
    t, sig = sign('GET', '/v1.0/token?grant_type=1')
    r = requests.get(BASE_URL + '/v1.0/token?grant_type=1',
                     headers={'client_id': ACCESS_ID, 'sign': sig, 't': t, 'sign_method': 'HMAC-SHA256'})
    r.raise_for_status()
    return r.json()['result']['access_token']

def get_device_data():
    token = get_token()
    t, sig = sign('GET', f'/v1.0/devices/{DEVICE_ID}/status', '', token)
    headers = {'client_id': ACCESS_ID, 'access_token': token, 'sign': sig, 't': t, 'sign_method': 'HMAC-SHA256'}
    r = requests.get(BASE_URL + f'/v1.0/devices/{DEVICE_ID}/status', headers=headers)
    r.raise_for_status()
    data = r.json()['result']
    switch  = next((d['value'] for d in data if d['code'] == 'switch_1'), False)
    voltage = next((d['value'] / 10   for d in data if d['code'] == 'cur_voltage'), 0)
    current = next((d['value'] / 1000 for d in data if d['code'] == 'cur_current'), 0)
    power   = next((d['value']        for d in data if d['code'] == 'cur_power'),   0)
    return voltage, current, power, switch

# ------------- API: Live Data (Every Second) -------------
@app.route('/api/live')
def api_live():
    try:
        voltage, current, power, switch = get_device_data()
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    ts = int(time.time())
    conn = sqlite3.connect('power.db')
    conn.execute('INSERT OR IGNORE INTO readings VALUES (?,?,?,?)',
                 (ts, voltage, current, power))
    conn.commit()

    # fetch last 24h power data (per second)
    rows = conn.execute('SELECT ts, power FROM readings WHERE ts>? ORDER BY ts',
                        (ts - 86400,)).fetchall()
    conn.close()

    return jsonify({
        'switch': switch,
        'voltage': voltage,
        'current': current,
        'power': power,
        'history': [{'x': r[0] * 1000, 'y': r[1]} for r in rows]
    })

# ------------- API: Hourly Average Data -------------
@app.route('/api/hourly')
def api_hourly():
    ts = int(time.time())
    conn = sqlite3.connect('power.db')
    rows = conn.execute('''
        SELECT 
            strftime('%Y-%m-%d %H:00:00', datetime(ts, 'unixepoch')) as hour,
            AVG(power) as avg_power
        FROM readings
        WHERE ts > ?
        GROUP BY hour
        ORDER BY hour
    ''', (ts - 86400,)).fetchall()
    conn.close()
    return jsonify([{'hour': r[0], 'avg_power': r[1]} for r in rows])

# ------------- API: Switch Control -------------
@app.route('/switch', methods=['POST'])
def switch_power():
    on = request.json.get('on', False)
    token = get_token()
    body = json.dumps({"commands": [{"code": "switch_1", "value": on}]})
    t, sig = sign('POST', f'/v1.0/devices/{DEVICE_ID}/commands', body, token)
    headers = {
        'client_id': ACCESS_ID,
        'access_token': token,
        'sign': sig,
        't': t,
        'sign_method': 'HMAC-SHA256',
        'Content-Type': 'application/json'
    }
    r = requests.post(BASE_URL + f'/v1.0/devices/{DEVICE_ID}/commands', headers=headers, data=body)
    return jsonify({'success': r.status_code == 200})

# ------------- API: Manual Save -------------
@app.route('/save', methods=['POST'])
def manual_save():
    data = request.json
    ts = int(time.time())
    conn = sqlite3.connect('power.db')
    conn.execute('INSERT OR IGNORE INTO readings VALUES (?,?,?,?)',
                 (ts, data.get('voltage'), data.get('current'), data.get('power')))
    conn.commit()
    conn.close()
    return jsonify({'saved': True})

# ------------- API: Download CSV -------------
@app.route('/download')
def download_csv():
    conn = sqlite3.connect('power.db')
    rows = conn.execute(
        "SELECT datetime(ts,'unixepoch') as dt, voltage, current, power FROM readings ORDER BY ts DESC"
    ).fetchall()
    conn.close()

    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(['Date-Time', 'Voltage(V)', 'Current(A)', 'Power(W)'])
    cw.writerows(rows)

    output = io.BytesIO()
    output.write(si.getvalue().encode('utf-8'))
    output.seek(0)

    return Response(output, mimetype="text/csv",
                    headers={"Content-Disposition": "attachment;filename=power_24h.csv"})

# ------------- Frontend Entry Point -------------
@app.route('/')
def index():
    return render_template('dashboard.html')  # Make sure templates/dashboard.html exists!

# ------------- Run App -------------
if __name__ == '__main__':
    app.run(debug=True)