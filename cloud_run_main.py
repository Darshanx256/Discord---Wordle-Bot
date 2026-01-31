"""
Cloud Run entry point - Flask web server only
No Discord bot integration for now - focus on getting web server running
"""
import os
import sys
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

logger.info("🚀 Cloud Run entry point starting...")

# Check for required environment variables
required_vars = ['DISCORD_TOKEN', 'SUPABASE_URL', 'SUPABASE_KEY']
for var in required_vars:
    if not os.getenv(var):
        logger.warning(f"⚠️  Environment variable {var} not set (will fail later if needed)")

logger.info("✅ Environment checks complete")

# Create minimal Flask app
try:
    from flask import Flask, jsonify, send_from_directory
    logger.info("✅ Flask imported successfully")
except Exception as e:
    logger.error(f"❌ Flask import failed: {e}", exc_info=True)
    sys.exit(1)

# Setup Flask app
app = Flask(__name__)

# Get static directory
base_dir = os.path.dirname(os.path.abspath(__file__))
static_dir = os.path.join(base_dir, 'static')

@app.route('/')
def home():
    """Serve homepage"""
    try:
        return send_from_directory(static_dir, 'index.html')
    except:
        return jsonify({'message': 'Wordle Bot - Discord bot with web interface'})

@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({'status': 'healthy', 'timestamp': str(__import__('datetime').datetime.now())}), 200

@app.route('/ready')
def ready():
    """Readiness endpoint"""
    return jsonify({'status': 'ready'}), 200

@app.route('/api/stats')
def stats():
    """Bot stats endpoint"""
    return jsonify({
        'server_count': 0,
        'uptime': 'N/A',
        'status': 'Cloud Run'
    }), 200

@app.errorhandler(404)
def not_found(e):
    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(500)
def server_error(e):
    logger.error(f"Server error: {e}")
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == "__main__":
    # Get port from environment
    port = int(os.environ.get('PORT', 8080))
    
    logger.info(f"📡 Starting Flask server on 0.0.0.0:{port}")
    
    try:
        from waitress import serve
        logger.info("Using Waitress WSGI server")
        serve(app, host='0.0.0.0', port=port, _quiet=True)
    except ImportError:
        logger.warning("Waitress not available, using Flask dev server")
        app.run(host='0.0.0.0', port=port, debug=False)
    except KeyboardInterrupt:
        logger.info("⚠️ Shutdown received")
        sys.exit(0)
    except Exception as e:
        logger.error(f"❌ Server error: {e}", exc_info=True)
        sys.exit(1)

