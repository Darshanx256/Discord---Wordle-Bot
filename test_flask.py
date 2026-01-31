"""
Minimal Cloud Run Flask server test
"""
import os
from flask import Flask, jsonify

app = Flask(__name__)

@app.route('/')
def hello():
    return jsonify({'message': 'Hello from Cloud Run'})

@app.route('/health')
def health():
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    from waitress import serve
    print(f"Starting server on port {port}")
    serve(app, host='0.0.0.0', port=port)
