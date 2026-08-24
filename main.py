"""The single WSGI entrypoint.

There were three: ``app.py`` (waitress, 16 threads), ``wsgi.py`` (waitress, 12
threads) and ``main.py`` (used by the platform, gunicorn, 8 threads). Three
files creating the app three ways with three server configurations means local
behaviour does not predict production behaviour, and a fix applied to one is
missing from the others. The other two are gone; this is the only one.

Production::

    gunicorn --config gunicorn.conf.py main:app

Development::

    python main.py                  # reloader on, single process
    flask --app main run --debug    # equivalent, via the Flask CLI

``app`` is created at import time because that is what a WSGI server expects to
find. ``create_app()`` is importable directly for tests and scripts.
"""
from app import create_app
from config import get_config

app = create_app()


if __name__ == '__main__':
    import os

    config = get_config()
    port = int(os.getenv('PORT', '5000'))
    debug = bool(getattr(config, 'DEBUG', False))

    if debug:
        print(f'Scriptly (development) -> http://localhost:{port}')
        print('Auto-reload is on. Do not use this server in production.')
        app.run(host='0.0.0.0', port=port, debug=True, use_reloader=True)
    else:
        # A production-ish local run without gunicorn, which does not exist on
        # Windows. Waitress is a real WSGI server; the development server is
        # single-threaded and explicitly not one.
        from waitress import serve

        threads = int(os.getenv('WAITRESS_THREADS', '8'))
        print(f'Scriptly -> http://localhost:{port} (waitress, {threads} threads)')
        serve(
            app,
            host='0.0.0.0',
            port=port,
            threads=threads,
            connection_limit=200,
            # AI routes can hold a connection for minutes. A short timeout cut
            # generation requests off mid-flight.
            channel_timeout=300,
            # Identify as the app, not as the server version.
            ident='Scriptly',
        )
