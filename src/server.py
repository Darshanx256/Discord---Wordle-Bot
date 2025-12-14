import os
from flask import Flask, send_from_directory

def run_flask_server():
    # Determine absolute path to the static folder (one level up from src)
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    static_dir = os.path.join(base_dir, 'static')
    
    # Initialize Flask App
    app = Flask(__name__, static_folder=static_dir)

    # --- ROUTE HANDLERS ---
    
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
    def levelup_ss():
        return send_from_directory(app.static_folder, 'screenshot-solo.png')

    @app.route('/screenshot-victory.png')
    def levelup_ss():
        return send_from_directory(app.static_folder, 'screenshot-victory.png')


    # --- SERVER RUN CONFIGURATION ---
    port = int(os.environ.get('PORT', 10000))
    # Using waitress for production-ready WSGI server
    from waitress import serve
    print(f"🌍 Starting Web Server on port {port}...")
    serve(app, host='0.0.0.0', port=port)
