from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, abort, current_app
from functools import wraps
from urllib.parse import urlparse
import requests

from app.utils.cache import SimpleCache

optimization_bp = Blueprint('optimization', __name__)
_cache = SimpleCache()

AHREFS_HOST = "ahrefs-url-research.p.rapidapi.com"
CACHE_TTL = 30 * 60  # 30 minutes


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('auth_bp.login'))
        if session.get('user_role') != 'ADMIN':
            abort(404)
        return f(*args, **kwargs)
    return decorated_function


def _validate_url(url):
    if not url or not url.strip():
        return None
    url = url.strip()
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    parsed = urlparse(url)
    if not parsed.netloc or '.' not in parsed.netloc:
        return None
    return url


@optimization_bp.route('/optimization')
@admin_required
def optimization_page():
    return render_template('optimization.html')


@optimization_bp.route('/api/optimization/url-metrics')
@admin_required
def url_metrics():
    url = request.args.get('url', '').strip()
    validated_url = _validate_url(url)
    if not validated_url:
        return jsonify({"success": False, "error": "Please enter a valid URL."}), 400

    cache_key = f"ahrefs_url_metrics:{validated_url}"
    cached = _cache.get(cache_key)
    if cached:
        return jsonify({"success": True, "data": cached, "cached": True})

    api_key = current_app.config.get('AHREFS_RAPIDAPI_KEY')
    if not api_key:
        return jsonify({"success": False, "error": "API key not configured."}), 500

    try:
        response = requests.get(
            f"https://{AHREFS_HOST}/url-metrics",
            params={"url": validated_url},
            headers={
                "x-rapidapi-key": api_key,
                "x-rapidapi-host": AHREFS_HOST
            },
            timeout=30
        )

        if response.status_code == 429:
            return jsonify({"success": False, "error": "API rate limit reached. Please try again later."}), 429

        if response.status_code == 403:
            return jsonify({"success": False, "error": "API access denied. Check your subscription."}), 403

        if response.status_code != 200:
            current_app.logger.warning(
                f"Ahrefs API returned {response.status_code}: {response.text[:200]}"
            )
            return jsonify({"success": False, "error": f"API returned status {response.status_code}."}), 502

        raw = response.json()
        api_data = raw.get("data", raw) if raw.get("success") else raw
        page = api_data.get("page", {}) if isinstance(api_data, dict) else {}
        domain = api_data.get("domain", {}) if isinstance(api_data, dict) else {}

        data = {**page, **domain}
        data["page_backlinks"] = page.get("backlinks")
        data["page_traffic"] = page.get("traffic")
        data["page_refDomains"] = page.get("refDomains")
        data["urlRating"] = page.get("urlRating")
        data["numberOfWordsOnPage"] = page.get("numberOfWordsOnPage")

        _cache.set(cache_key, data, ttl=CACHE_TTL)
        return jsonify({"success": True, "data": data, "cached": False})

    except requests.exceptions.Timeout:
        return jsonify({"success": False, "error": "Request timed out. The API may be temporarily slow — please try again."}), 504
    except requests.exceptions.ConnectionError:
        return jsonify({"success": False, "error": "Could not connect to the API. Check your network."}), 502
    except requests.exceptions.RequestException as e:
        current_app.logger.error(f"Ahrefs API request failed: {e}")
        return jsonify({"success": False, "error": "Failed to connect to the API."}), 502
    except ValueError:
        return jsonify({"success": False, "error": "Invalid response from API."}), 502
