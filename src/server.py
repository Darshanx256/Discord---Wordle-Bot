import os
import json
import signal
import sys
import asyncio
from flask import Flask, send_from_directory, jsonify, request
from flask_compress import Compress

# Global reference to bot for graceful shutdown
_bot_instance = None

def set_bot_instance(bot):
    """Set the bot instance for graceful shutdown."""
    global _bot_instance
    _bot_instance = bot

def create_flask_app():
    """Create and configure Flask app."""
    # Determine absolute path to the static folder (one level up from src)
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    static_dir = os.path.join(base_dir, 'static')
    
    # Initialize Flask App
    app = Flask(__name__, static_folder=static_dir)
    
    # --- COMPRESSION (gzip/brotli) ---
    # Brotli is preferred when supported by browser (20-26% better than gzip)
    app.config['COMPRESS_ALGORITHM'] = ['br', 'gzip', 'deflate']
    app.config['COMPRESS_MIMETYPES'] = [
        'text/html', 'text/css', 'text/javascript', 'application/javascript',
        'application/json', 'image/svg+xml'
    ]
    app.config['COMPRESS_MIN_SIZE'] = 500  # Only compress files > 500 bytes
    Compress(app)
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
    @app.route('/tos')
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

    # Health check endpoint for Cloud Run
    @app.route('/health')
    def health():
        """Health check endpoint for Cloud Run / load balancers."""
        try:
            # Check if bot is connected
            if _bot_instance and _bot_instance.is_closed():
                return jsonify({'status': 'error', 'message': 'Bot disconnected'}), 503
            
            return jsonify({'status': 'healthy', 'bot_ready': _bot_instance is not None}), 200
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 503

    # Readiness check endpoint for Cloud Run
    @app.route('/ready')
    def ready():
        """Readiness check endpoint for Cloud Run."""
        try:
            if _bot_instance and _bot_instance.is_ready():
                return jsonify({'status': 'ready'}), 200
            return jsonify({'status': 'not_ready'}), 503
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 503

    # Graceful shutdown endpoint
    @app.route('/shutdown', methods=['POST'])
    def shutdown():
        """Graceful shutdown endpoint for Cloud Run."""
        if _bot_instance:
            # Close bot connection gracefully
            asyncio.create_task(_bot_instance.close())
        return jsonify({'status': 'shutting_down'}), 200

    return app

def run_flask_server():
    """Legacy function for backward compatibility - creates and runs Flask server."""
    # Using waitress for production-ready WSGI server
    from waitress import serve
    
    app = create_flask_app()
    port = int(os.environ.get('PORT', 8080))
    
    print(f"🌍 Starting Web Server on port {port}...")
    print(f"🔗 Health checks available at: http://localhost:{port}/health")
    
    # Set up graceful shutdown handlers for Cloud Run
    def handle_sigterm(signum, frame):
        print("⚠️ SIGTERM received, gracefully shutting down...")
        if _bot_instance:
            sys.exit(0)
    
    signal.signal(signal.SIGTERM, handle_sigterm)
    
    try:
        serve(app, host='0.0.0.0', port=port, _quiet=False)
