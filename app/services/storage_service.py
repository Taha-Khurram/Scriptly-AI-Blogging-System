"""Object storage for user uploads.

Uploads were written to ``app/static/uploads/gallery/<user_id>/`` on the
container's local filesystem. On any container platform that disk is ephemeral:
every deploy, restart or scale event wipes it. The Firestore metadata survives,
so users were left with a gallery full of permanently broken image links and no
recovery path -- and the same local path makes horizontal scaling impossible,
because an image uploaded to instance A is a 404 on instance B.

This module puts the storage backend behind one interface:

``FirebaseStorageBackend``
    Firebase Storage, already a configured project dependency. Survives
    deploys, shared across instances, and serves bytes without occupying a
    worker thread.

``LocalStorageBackend``
    The previous on-disk behaviour, kept for offline development where the
    emulator or a real bucket is not available. It logs a warning at startup so
    it can never be mistaken for a production configuration.

Both validate the upload the same way. Extension checks alone are not enough --
the extension is attacker-chosen -- so the *content* is inspected: the magic
bytes must match a known image format, and the stored content type comes from
that inspection rather than from the client's ``Content-Type`` header.
"""
from __future__ import annotations

import io
import os
import time
import uuid

from app.core.errors import ConfigurationError, PayloadTooLargeError, ValidationError
from app.core.logging import get_logger

logger = get_logger(__name__)

# Extension -> canonical content type. The keys are also the allowlist.
ALLOWED_IMAGE_TYPES = {
    'png': 'image/png',
    'jpg': 'image/jpeg',
    'jpeg': 'image/jpeg',
    'gif': 'image/gif',
    'webp': 'image/webp',
}

# Magic-byte signatures, checked against the file's actual first bytes.
# (offset, signature, extension). WEBP needs the RIFF container *and* the
# 'WEBP' fourcc at offset 8, or any RIFF file (a .wav) would pass as an image.
_SIGNATURES = (
    (0, b'\x89PNG\r\n\x1a\n', 'png'),
    (0, b'\xff\xd8\xff', 'jpg'),
    (0, b'GIF87a', 'gif'),
    (0, b'GIF89a', 'gif'),
    (0, b'RIFF', 'webp'),          # confirmed below by the fourcc check
)

# Read for signature detection. 32 bytes covers every signature above.
_SNIFF_BYTES = 32


def detect_image_type(data):
    """Return the extension implied by ``data``'s magic bytes, or ``None``.

    Content-based rather than name-based: a file called ``photo.png`` that is
    actually an HTML document would be served from the application's own origin
    and, in a browser that content-sniffs, executed there. Deciding the type
    from the bytes and then setting ``Content-Type`` from *that* is what makes
    the ``nosniff`` header meaningful.
    """
    if not data or len(data) < 4:
        return None
    for offset, signature, extension in _SIGNATURES:
        if not data.startswith(signature, offset):
            continue
        if extension == 'webp':
            if len(data) < 12 or data[8:12] != b'WEBP':
                continue
        return extension
    return None


def _human_size(byte_count):
    """Byte count as a short human string, never rounding down to ``0 MB``.

    The naive ``bytes // 1_048_576`` form reported a 900 KB limit as "0 MB",
    which reads as a bug to the person hitting it.
    """
    if byte_count >= 1_048_576:
        return f'{byte_count / 1_048_576:.1f} MB'.replace('.0 ', ' ')
    if byte_count >= 1024:
        return f'{byte_count / 1024:.0f} KB'
    return f'{byte_count} bytes'


def validate_image(data, declared_filename, max_bytes):
    """Validate an upload and return ``(extension, content_type)``.

    Raises :class:`PayloadTooLargeError` or :class:`ValidationError` with a
    message safe to show the user.
    """
    if not data:
        raise ValidationError('The uploaded file is empty.')

    if len(data) > max_bytes:
        raise PayloadTooLargeError(
            f'That file is {_human_size(len(data))}. '
            f'The limit is {_human_size(max_bytes)}.'
        )

    actual = detect_image_type(data)
    if actual is None:
        raise ValidationError(
            'That file is not a PNG, JPEG, GIF or WebP image.'
        )

    declared = (declared_filename or '').rsplit('.', 1)
    declared_ext = declared[1].lower() if len(declared) == 2 else ''
    if declared_ext and declared_ext not in ALLOWED_IMAGE_TYPES:
        raise ValidationError(
            'File type not allowed. Use png, jpg, jpeg, gif or webp.'
        )

    # A mismatch is not necessarily an attack -- a .jpeg holding PNG bytes is a
    # common rename -- so it is logged and the *detected* type wins.
    if declared_ext and ALLOWED_IMAGE_TYPES.get(declared_ext) != ALLOWED_IMAGE_TYPES[actual]:
        logger.info(
            'Upload extension disagrees with content; trusting content',
            extra={'declared': declared_ext, 'detected': actual},
        )

    return actual, ALLOWED_IMAGE_TYPES[actual]


def build_object_name(user_id, extension):
    """A collision-free, non-guessable storage key scoped to one user.

    The uuid matters: a predictable key would let anyone enumerate another
    user's uploads from the public bucket URL.
    """
    return f'gallery/{user_id}/{int(time.time())}_{uuid.uuid4().hex[:12]}.{extension}'


class StorageBackend:
    """Interface both backends implement."""

    name = 'abstract'

    def save(self, object_name, data, content_type):
        raise NotImplementedError

    def delete(self, url):
        raise NotImplementedError

    def healthy(self):
        raise NotImplementedError


class FirebaseStorageBackend(StorageBackend):
    """Firebase Storage (Google Cloud Storage under the hood)."""

    name = 'firebase'

    def __init__(self):
        from app.firebase.firebase_admin import FirebaseLoader

        self._bucket = FirebaseLoader.get_bucket()
        if self._bucket is None:
            raise ConfigurationError(
                'FB_STORAGE_BUCKET is not configured, so uploads cannot be '
                'stored durably. Set it, or set UPLOAD_BACKEND=local for '
                'local development only.'
            )

    def save(self, object_name, data, content_type):
        blob = self._bucket.blob(object_name)
        blob.upload_from_string(data, content_type=content_type)
        # Gallery images are embedded in published posts, so they have to be
        # readable without a signed URL. Nothing else lives under this prefix.
        blob.make_public()
        # Long max-age with an immutable hint: the object name contains a uuid,
        # so a given URL's bytes never change and a CDN can hold it forever.
        blob.cache_control = 'public, max-age=31536000, immutable'
        blob.patch()
        logger.info(
            'Stored upload in Firebase Storage',
            extra={'object': object_name, 'bytes': len(data)},
        )
        return blob.public_url

    def delete(self, url):
        object_name = self._object_name_from_url(url)
        if not object_name:
            logger.warning('Could not derive object name from URL: %s', url[:120])
            return False
        try:
            self._bucket.blob(object_name).delete()
            return True
        except Exception:
            # Already gone is the common case and is not a failure: the
            # metadata is what the user sees, and that has been removed.
            logger.info('Storage object already absent: %s', object_name)
            return False

    @staticmethod
    def _object_name_from_url(url):
        """Recover the object key from a public bucket URL."""
        if not url:
            return None
        from urllib.parse import unquote, urlparse

        path = urlparse(url).path.lstrip('/')
        # https://storage.googleapis.com/<bucket>/<object>
        if path.startswith('storage.googleapis.com/'):
            path = path.split('/', 1)[1]
        parts = path.split('/', 1)
        if len(parts) == 2 and parts[0] and not parts[0].startswith('gallery'):
            path = parts[1]
        # .../o/<url-encoded object>
        if '/o/' in path:
            path = path.split('/o/', 1)[1].split('?', 1)[0]
        return unquote(path) or None

    def healthy(self):
        try:
            return self._bucket.exists()
        except Exception:
            return False


class LocalStorageBackend(StorageBackend):
    """On-disk storage under ``app/static/uploads``. Development only."""

    name = 'local'

    def __init__(self, root=None):
        self._root = root or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'static', 'uploads',
        )
        os.makedirs(self._root, exist_ok=True)
        logger.warning(
            'Upload backend is LOCAL DISK (%s). Files are lost on every '
            'deploy or restart and are invisible to other instances. Set '
            'UPLOAD_BACKEND=firebase for anything but local development.',
            self._root,
        )

    def _absolute(self, object_name):
        """Resolve an object name inside the root, refusing to escape it."""
        candidate = os.path.normpath(os.path.join(self._root, object_name))
        root = os.path.normpath(self._root)
        try:
            if os.path.commonpath([candidate, root]) != root:
                return None
        except ValueError:
            # Raised on Windows when the paths are on different drives, which
            # is itself a reason to refuse.
            return None
        return candidate

    def save(self, object_name, data, content_type):
        path = self._absolute(object_name)
        if path is None:
            raise ValidationError('Invalid upload destination.')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with io.open(path, 'wb') as handle:
            handle.write(data)
        return f'/static/uploads/{object_name}'

    def delete(self, url):
        if not url:
            return False
        object_name = url.split('/static/uploads/', 1)[-1]
        path = self._absolute(object_name)
        if path is None:
            logger.warning('Refusing delete outside the uploads root: %s', url[:120])
            return False
        try:
            if os.path.exists(path):
                os.remove(path)
                return True
        except OSError:
            logger.warning('Could not delete %s', path, exc_info=True)
        return False

    def healthy(self):
        return os.path.isdir(self._root) and os.access(self._root, os.W_OK)


class StorageService:
    """Front door. Chooses a backend at startup and never changes it."""

    def __init__(self):
        self._backend = None
        self._legacy = None
        self._max_bytes = 5 * 1024 * 1024

    def configure(self, *, backend='firebase', max_bytes=5 * 1024 * 1024):
        """Select the backend. Called once from the app factory.

        A Firebase backend that cannot initialise falls back to local disk with
        a loud warning rather than refusing to boot: the gallery is one feature,
        and taking the whole application down over it would be the larger
        outage. ``/healthz`` reports which backend is live.
        """
        self._max_bytes = max_bytes
        if backend == 'local':
            self._backend = LocalStorageBackend()
            return

        try:
            self._backend = FirebaseStorageBackend()
            logger.info('Upload backend: Firebase Storage')
        except Exception as exc:
            logger.error(
                'Firebase Storage unavailable (%s); falling back to local disk. '
                'Uploads will NOT survive a deploy.', exc,
            )
            self._backend = LocalStorageBackend()

    @property
    def backend_name(self):
        return self._backend.name if self._backend else 'unconfigured'

    @property
    def is_durable(self):
        """Whether stored files survive a restart and are visible cluster-wide."""
        return isinstance(self._backend, FirebaseStorageBackend)

    @property
    def max_bytes(self):
        return self._max_bytes

    def _require(self):
        if self._backend is None:
            # Lazily fall back rather than raising: a unit test that touches
            # the service without running the factory should still work.
            self.configure(backend='local', max_bytes=self._max_bytes)
        return self._backend

    def save_image(self, user_id, data, filename):
        """Validate and store an image. Returns ``(url, content_type, size)``."""
        extension, content_type = validate_image(data, filename, self._max_bytes)
        object_name = build_object_name(user_id, extension)
        url = self._require().save(object_name, data, content_type)
        return url, content_type, len(data)

    def delete(self, url):
        """Delete by URL, routing to whichever backend actually holds it.

        Necessary during and after the migration to durable storage: rows
        written before it still carry ``/static/uploads/...`` paths, and asking
        the Firebase backend to delete one of those would derive a nonsense
        object key and silently leave the file behind. The URL shape says
        unambiguously where the bytes live, so it decides.
        """
        if not url:
            return False
        if '/static/uploads/' in url:
            return self._legacy_local().delete(url)
        return self._require().delete(url)

    def _legacy_local(self):
        """A cached local backend for pre-migration URLs.

        Cached because constructing one logs the "local disk" warning, and a
        bulk delete of forty legacy images should not emit it forty times.
        """
        if isinstance(self._backend, LocalStorageBackend):
            return self._backend
        if self._legacy is None:
            self._legacy = LocalStorageBackend()
        return self._legacy

    def healthy(self):
        return self._require().healthy()


# Module-level singleton, configured by the app factory.
storage = StorageService()
