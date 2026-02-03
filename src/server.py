import os
import json
from flask import Flask, jsonify
from flask_cors import CORS

def run_flask_server():
    """Minimal API-only server for bot stats - no static file hosting."""
    # Initialize Flask App (no static folder needed)
    app = Flask(__name__)
    
    # Enable CORS for external website access
    CORS(app, resources={r"/api/*": {"origins": "*"}})
    
    # Determine path for stats file
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    stats_file = os.path.join(base_dir, 'bot_stats.json')
    
    @app.route('/api/stats')
    def api_stats():
        """Return bot statistics for external website consumption."""
        try:
            if os.path.exists(stats_file):
                with open(stats_file, 'r') as f:
                    return jsonify(json.load(f))
        except Exception as e:
            print(f"⚠️ Error reading stats file: {e}")
        
        # Return defaults if file doesn't exist or error occurred
        return jsonify({
            'server_count': 0,
            'simple_words': 600,
            'classic_words': 2800,
            'total_words': 13000,
            'last_updated': None
        })
    
    @app.route('/health')
    def health():
        """Health check endpoint."""
        return jsonify({'status': 'ok'})

    # --- SERVER RUN CONFIGURATION ---
    port = int(os.environ.get('PORT', 8080))
    # Using waitress for production-ready WSGI server
    from waitress import serve
    print(f"🌍 Starting Minimal API Server on port {port}...")
    print(f"📊 Serving stats at /api/stats with CORS enabled")
    serve(app, host='0.0.0.0', port=port)
