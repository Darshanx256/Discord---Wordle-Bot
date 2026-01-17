import os
import json
from flask import Flask, send_from_directory, jsonify, request

def run_flask_server():
    # Determine absolute path to the static folder (one level up from src)
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    static_dir = os.path.join(base_dir, 'static')
    
    # Initialize Flask App
    app = Flask(__name__, static_folder=static_dir)

    # --- CACHE HEADERS ---
    @app.after_request
    def add_cache_headers(response):
        """Add cache-control and expiry headers for better performance."""
        # Get the request path
        path = request.path.lower()
        
        # Static assets (images, CSS) - cache for 1 week
        if path.endswith(('.png', '.jpg', '.jpeg', '.ico', '.css')):
            response.cache_control.max_age = 604800  # 1 week
            response.cache_control.public = True
        # HTML pages - cache for 1 hour (allows updates to propagate)
        elif path.endswith('.html') or path in ['/', '/terms', '/privacy']:
            response.cache_control.max_age = 3600  # 1 hour
            response.cache_control.public = True
        # API endpoints - no cache
        elif path.startswith('/api/'):
            response.cache_control.no_cache = True
            response.cache_control.no_store = True
        
        return response
    
    # 1. Homepage Route (Serving index.html)
    @app.route('/')
    def home():
        return send_from_directory(app.static_folder, 'index.html')

    # 2. Terms of Service Route (Serving tos.html)
    @app.route('/terms')
    def terms():
        return send_from_directory(app.static_folder, 'tos.html')

    # 3. Privacy Policy Route (Serving privacy.html)
    @app.route('/privacy')
    def privacy():
        return send_from_directory(app.static_folder, 'privacy.html')

    @app.route('/favicon.ico')
    def favicon():
        return send_from_directory(app.static_folder, 'favicon.ico')

    @app.route('/icon.png')
    def icon():
        return send_from_directory(app.static_folder, 'icon.png')

    @app.route('/styles.css')
    def stylesheet():
        return send_from_directory(app.static_folder, 'styles.css')

    @app.route('/screenshot-leaderboard.jpg')
    def leaderboard_ss():
        return send_from_directory(app.static_folder, 'screenshot-leaderboard.jpg')

    @app.route('/screenshot-levelup.png')
    def levelup_ss():
        return send_from_directory(app.static_folder, 'screenshot-levelup.png')

    @app.route('/screenshot-solo.png')
    def solo_ss():
        return send_from_directory(app.static_folder, 'screenshot-solo.png')

    @app.route('/screenshot-victory.png')
    def victory_ss():
        return send_from_directory(app.static_folder, 'screenshot-victory.png')

    # API endpoint for bot stats
    @app.route('/api/stats')
    def api_stats():
        stats_file = os.path.join(app.static_folder, 'bot_stats.json')
        try:
            if os.path.exists(stats_file):
                with open(stats_file, 'r') as f:
                    return jsonify(json.load(f))
        except:
            pass
        # Return defaults if file doesn't exist
        return jsonify({
            'server_count': 0,
            'simple_words': 600,
            'classic_words': 2800,
            'total_words': 13000,
            'last_updated': None
        })


    # --- SERVER RUN CONFIGURATION ---
    port = int(os.environ.get('PORT', 10000))
    # Using waitress for production-ready WSGI server
    from waitress import serve
    print(f"🌍 Starting Web Server on port {port}...")
    serve(app, host='0.0.0.0', port=port)
