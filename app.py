from app import create_app
from waitress import serve
import logging
import os

app = create_app()

logger = logging.getLogger('waitress')
logger.setLevel(logging.INFO)

if __name__ == "__main__":
    debug = os.environ.get('FLASK_DEBUG', '1') == '1'

    if debug:
        print("[DEBUG] ScriptlyAI running at http://localhost:5000 (auto-reload enabled)")
        app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=True)
    else:
        print("[INFO] ScriptlyAI is running at http://localhost:5000")
        serve(
            app,
            host='0.0.0.0',
            port=5000,
            threads=16,
            connection_limit=200,
            channel_timeout=300,
            recv_bytes=65536,
            send_bytes=65536,
        )
        
        
