import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.getenv('SECRET_KEY')
    FIREBASE_SERVICE_ACCOUNT = os.getenv('FIREBASE_SERVICE_ACCOUNT')
    GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

    # Session timeout: expire after 15 minutes of inactivity
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=15)
    SESSION_REFRESH_EACH_REQUEST = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'

    # RapidAPI for SEO Keyword Research (seo-keyword-research.p.rapidapi.com)
    # Get your key at: https://rapidapi.com/developer_jeeva_k/api/seo-keyword-research
    RAPIDAPI_KEY = os.getenv('RAPIDAPI_KEY')

    # RapidAPI for Ahrefs URL Research (ahrefs-url-research.p.rapidapi.com)
    AHREFS_RAPIDAPI_KEY = os.getenv('AHREFS_RAPIDAPI_KEY')

    # Google OAuth for Analytics integration
    GOOGLE_OAUTH_CLIENT_ID = os.getenv('GOOGLE_OAUTH_CLIENT_ID')
    GOOGLE_OAUTH_CLIENT_SECRET = os.getenv('GOOGLE_OAUTH_CLIENT_SECRET')

    # Firebase Web SDK Configuration (from environment variables)
    FIREBASE_CONFIG = {
        "apiKey": os.getenv("FB_API_KEY"),
        "authDomain": os.getenv("FB_AUTH_DOMAIN"),
        "projectId": os.getenv("FB_PROJECT_ID"),
        "storageBucket": os.getenv("FB_STORAGE_BUCKET"),
        "messagingSenderId": os.getenv("FB_SENDER_ID"),
        "appId": os.getenv("FB_APP_ID"),
        "measurementId": os.getenv("FB_MEASUREMENT_ID")
    }