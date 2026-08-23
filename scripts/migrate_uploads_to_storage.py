"""Move existing local gallery uploads into durable object storage.

Rows written before the storage change carry ``/static/uploads/gallery/...``
URLs pointing at the container's local disk. Those files are one deploy away
from being gone while their Firestore metadata survives, which is what leaves a
user's library full of broken images. This script uploads each still-present
file to Firebase Storage and rewrites the row's ``url`` to the durable one.

Safe to interrupt and safe to re-run:

* Rows already carrying a non-local URL are skipped.
* Each row is uploaded first and its metadata updated only on success, so a
  crash leaves an unreferenced object (harmless, cleanable) rather than a row
  pointing at nothing.
* The local file is left in place. Deleting it is a separate, deliberate step
  once the migration has been verified -- see ``--delete-local``.

Usage::

    # See what would happen, change nothing:
    python scripts/migrate_uploads_to_storage.py --dry-run

    # Migrate for real:
    python scripts/migrate_uploads_to_storage.py

    # Migrate, then remove the local copies it successfully uploaded:
    python scripts/migrate_uploads_to_storage.py --delete-local
"""
from __future__ import annotations

import argparse
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

LOCAL_URL_PREFIX = '/static/uploads/'


def local_path_for(url):
    """Absolute on-disk path for a legacy URL, or ``None`` if it escapes.

    The URL is read back from Firestore, so it is treated as untrusted: a
    stored value containing ``..`` must not resolve to a read outside the
    uploads directory.
    """
    if not url or LOCAL_URL_PREFIX not in url:
        return None
    relative = url.split(LOCAL_URL_PREFIX, 1)[1]
    root = os.path.normpath(os.path.join(ROOT, 'app', 'static', 'uploads'))
    candidate = os.path.normpath(os.path.join(root, relative))
    try:
        if os.path.commonpath([candidate, root]) != root:
            return None
    except ValueError:
        return None
    return candidate


def iterate_gallery_rows(client):
    """Yield ``(doc_id, data)`` for every gallery image document."""
    for doc in client.collection('gallery_images').stream():
        yield doc.id, doc.to_dict() or {}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true',
                        help='report what would change without writing anything')
    parser.add_argument('--delete-local', action='store_true',
                        help='remove each local file after a successful upload')
    args = parser.parse_args()

    os.environ.setdefault('FLASK_ENV', 'production')

    from config import get_config
    from app.core.logging import get_logger
    from app.firebase.firebase_admin import FirebaseLoader
    from app.services.storage_service import (
        FirebaseStorageBackend, build_object_name, detect_image_type,
    )

    logger = get_logger('migrate_uploads')
    config = get_config()
    config.validate()

    client = FirebaseLoader.get_instance(config.FIREBASE_SERVICE_ACCOUNT)
    backend = FirebaseStorageBackend()

    stats = {'total': 0, 'already_durable': 0, 'migrated': 0,
             'missing_file': 0, 'unreadable': 0, 'failed': 0}

    for doc_id, data in iterate_gallery_rows(client):
        stats['total'] += 1
        url = data.get('url') or ''

        if LOCAL_URL_PREFIX not in url:
            stats['already_durable'] += 1
            continue

        path = local_path_for(url)
        if path is None:
            logger.warning('Refusing suspicious stored URL', extra={'doc': doc_id})
            stats['failed'] += 1
            continue

        if not os.path.exists(path):
            # The file is already gone -- exactly the data loss this change
            # prevents. Reported, not repaired: the bytes do not exist anywhere.
            logger.warning(
                'Local file already lost; metadata orphaned',
                extra={'doc': doc_id, 'url': url},
            )
            stats['missing_file'] += 1
            continue

        try:
            payload = io.open(path, 'rb').read()
        except OSError:
            logger.warning('Could not read local file', extra={'doc': doc_id})
            stats['unreadable'] += 1
            continue

        extension = detect_image_type(payload)
        if extension is None:
            logger.warning(
                'Stored file is not a recognised image; skipping',
                extra={'doc': doc_id, 'path': path},
            )
            stats['unreadable'] += 1
            continue

        user_id = data.get('user_id') or 'unknown'
        object_name = build_object_name(user_id, extension)

        if args.dry_run:
            print(f'WOULD MIGRATE {doc_id}  {url}  ->  {object_name}')
            stats['migrated'] += 1
            continue

        try:
            from app.services.storage_service import ALLOWED_IMAGE_TYPES
            new_url = backend.save(
                object_name, payload, ALLOWED_IMAGE_TYPES[extension]
            )
            # Metadata is updated only after the bytes are safely stored.
            client.collection('gallery_images').document(doc_id).update({
                'url': new_url,
                'storage_backend': 'firebase',
                'migrated_from': url,
            })
            stats['migrated'] += 1
            print(f'migrated {doc_id}  ->  {new_url}')

            if args.delete_local:
                try:
                    os.remove(path)
                except OSError:
                    logger.warning('Could not delete local copy', extra={'path': path})
        except Exception:
            logger.exception('Migration failed for %s', doc_id)
            stats['failed'] += 1

    print()
    print('--- Summary ---')
    for key, value in stats.items():
        print(f'{key.replace("_", " "):18} {value}')

    if stats['missing_file']:
        print()
        print(f'{stats["missing_file"]} row(s) reference files that no longer exist. '
              'Those images were lost before this migration ran; the rows can be '
              'deleted from the gallery UI.')

    return 1 if stats['failed'] else 0


if __name__ == '__main__':
    sys.exit(main())
