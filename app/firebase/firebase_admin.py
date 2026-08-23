import firebase_admin
from firebase_admin import credentials, firestore, storage
import json
import os
from app.core.logging import get_logger

logger = get_logger(__name__)

class FirebaseLoader:
    _instance = None
    _bucket = None

    @classmethod
    def get_instance(cls, cert_path_or_json=None):
        if cls._instance is None:
            firebase_creds = cert_path_or_json or os.getenv('FIREBASE_SERVICE_ACCOUNT')

            if not firebase_creds:
                logger.error("ERROR: No Firebase credentials found!")
                logger.info(f"FIREBASE_SERVICE_ACCOUNT env: {os.getenv('FIREBASE_SERVICE_ACCOUNT', 'NOT SET')[:50] if os.getenv('FIREBASE_SERVICE_ACCOUNT') else 'NOT SET'}")
                raise ValueError("Firebase credentials not found. Set FIREBASE_SERVICE_ACCOUNT environment variable.")

            cred = None

            if isinstance(firebase_creds, str) and os.path.exists(firebase_creds):
                logger.info(f"Loading Firebase from file: {firebase_creds}")
                cred = credentials.Certificate(firebase_creds)
            else:
                try:
                    if isinstance(firebase_creds, dict):
                        cert_dict = firebase_creds
                    else:
                        json_str = firebase_creds.strip()
                        cert_dict = json.loads(json_str)
                    logger.info(f"Loading Firebase from JSON (project: {cert_dict.get('project_id', 'unknown')})")
                    cred = credentials.Certificate(cert_dict)
                except json.JSONDecodeError as e:
                    logger.exception("JSON Parse Error")
                    logger.exception(f"Value type: {type(firebase_creds)}")
                    logger.exception(f"First 100 chars: {str(firebase_creds)[:100]}")
                    raise ValueError(f"Invalid Firebase JSON: {e}")
                except Exception as e:
                    logger.exception("Firebase Error")
                    raise ValueError(f"Invalid Firebase certificate: {e}")

            storage_bucket = os.getenv('FB_STORAGE_BUCKET')
            firebase_admin.initialize_app(cred, {'storageBucket': storage_bucket})
            cls._instance = firestore.client()
            if storage_bucket:
                cls._bucket = storage.bucket()
                logger.info(f"Firebase Storage bucket: {storage_bucket} ---")
            logger.info("Firebase Admin SDK Initialized Successfully ---")

        return cls._instance

    @classmethod
    def get_bucket(cls):
        if cls._bucket is None:
            cls.get_instance()
        return cls._bucket