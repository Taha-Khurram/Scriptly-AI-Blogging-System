# WSGI entrypoint used by Gunicorn in production (`gunicorn main:app`),
# e.g. the Render Blueprint in render.yaml.
from app import create_app

app = create_app()

if __name__ == '__main__':
    app.run(debug=True, threaded=True, port=5000)
