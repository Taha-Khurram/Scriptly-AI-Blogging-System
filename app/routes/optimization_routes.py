from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, abort, current_app
from functools import wraps
from urllib.parse import urlparse
import requests
import google.generativeai as genai
import os

from app.utils.cache import SimpleCache
from app.firebase.firestore_service import FirestoreService

optimization_bp = Blueprint('optimization', __name__)
_cache = SimpleCache()
_db = FirestoreService()

AHREFS_HOST = "ahrefs-url-research.p.rapidapi.com"
AHREFS_KEYWORD_HOST = "ahrefs-keyword-research.p.rapidapi.com"
SITE_AUDIT_HOST = "website-analyze-and-seo-audit-pro.p.rapidapi.com"
CACHE_TTL = 30 * 60  # 30 minutes

VALID_COUNTRIES = {
    'us', 'uk', 'ca', 'au', 'de', 'fr', 'es', 'it', 'br', 'in',
    'jp', 'nl', 'se', 'no', 'dk', 'fi', 'pl', 'ru', 'mx', 'ar'
}


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


@optimization_bp.route('/api/optimization/keyword-metrics')
@admin_required
def keyword_metrics():
    keyword = request.args.get('keyword', '').strip()
    country = request.args.get('country', 'us').strip().lower()

    if not keyword:
        return jsonify({"success": False, "error": "Please enter a keyword."}), 400

    if country not in VALID_COUNTRIES:
        country = 'us'

    cache_key = f"ahrefs_keyword:{keyword}:{country}"
    cached = _cache.get(cache_key)
    if cached:
        return jsonify({"success": True, "data": cached, "cached": True})

    api_key = current_app.config.get('AHREFS_RAPIDAPI_KEY')
    if not api_key:
        return jsonify({"success": False, "error": "API key not configured."}), 500

    try:
        response = requests.get(
            f"https://{AHREFS_KEYWORD_HOST}/keyword-metrics",
            params={"keyword": keyword, "country": country},
            headers={
                "x-rapidapi-key": api_key,
                "x-rapidapi-host": AHREFS_KEYWORD_HOST
            },
            timeout=30
        )

        if response.status_code == 429:
            return jsonify({"success": False, "error": "API rate limit reached. Please try again later."}), 429

        if response.status_code == 403:
            return jsonify({"success": False, "error": "API access denied. Check your subscription."}), 403

        if response.status_code != 200:
            current_app.logger.warning(
                f"Ahrefs Keyword API returned {response.status_code}: {response.text[:200]}"
            )
            return jsonify({"success": False, "error": f"API returned status {response.status_code}."}), 502

        raw = response.json()
        data = raw.get("data", raw) if raw.get("success") else raw

        _cache.set(cache_key, data, ttl=CACHE_TTL)
        return jsonify({"success": True, "data": data, "cached": False})

    except requests.exceptions.Timeout:
        return jsonify({"success": False, "error": "Request timed out. Please try again."}), 504
    except requests.exceptions.ConnectionError:
        return jsonify({"success": False, "error": "Could not connect to the API. Check your network."}), 502
    except requests.exceptions.RequestException as e:
        current_app.logger.error(f"Ahrefs Keyword API request failed: {e}")
        return jsonify({"success": False, "error": "Failed to connect to the API."}), 502
    except ValueError:
        return jsonify({"success": False, "error": "Invalid response from API."}), 502


def _extract_keywords_from_content(title, content):
    """Use Gemini to extract focus keywords from blog content."""
    genai.configure(api_key=os.getenv('GEMINI_API_KEY'))
    model = genai.GenerativeModel('gemini-flash-lite-latest')

    prompt = f"""Extract 5 high-value SEO keywords from this blog post.
Return ONLY a comma-separated list of keywords, nothing else.

Title: {title}
Content: {content[:1500]}

Focus on:
- Primary topic keywords
- Long-tail search phrases users would type
- High commercial/informational intent terms"""

    response = model.generate_content(prompt)
    text = (response.text or "") if response else ""
    if not text.strip():
        return [title.lower()] if title else []
    keywords = [kw.strip() for kw in text.split(',') if kw.strip()]
    return keywords[:5]


def _fetch_keyword_metrics(keyword, country, api_key):
    """Fetch metrics for a single keyword from Ahrefs API."""
    try:
        resp = requests.get(
            f"https://{AHREFS_KEYWORD_HOST}/keyword-metrics",
            params={"keyword": keyword, "country": country},
            headers={
                "x-rapidapi-key": api_key,
                "x-rapidapi-host": AHREFS_KEYWORD_HOST
            },
            timeout=30
        )
        if resp.status_code == 200:
            raw = resp.json()
            data = raw.get("data", raw) if raw.get("success") else raw
            return data
    except Exception:
        pass
    return None


@optimization_bp.route('/api/optimization/draft-keywords', methods=['POST'])
@admin_required
def draft_keywords():
    body = request.get_json(silent=True) or {}
    blog_id = body.get('blog_id', '').strip()
    country = body.get('country', 'us').strip().lower()

    if not blog_id:
        return jsonify({"success": False, "error": "Please select a draft."}), 400

    if country not in VALID_COUNTRIES:
        country = 'us'

    cache_key = f"draft_keywords:{blog_id}:{country}"
    cached = _cache.get(cache_key)
    if cached:
        return jsonify({"success": True, "data": cached, "cached": True})

    blog = _db.get_blog_by_id(blog_id)
    if not blog:
        return jsonify({"success": False, "error": "Draft not found."}), 404

    user_id = session.get('user_id')
    if blog.get('author_id') != user_id and blog.get('site_owner_id') != user_id:
        return jsonify({"success": False, "error": "Access denied."}), 403

    content = blog.get('content', '')
    if isinstance(content, dict):
        content = content.get('markdown') or content.get('body') or ''
    title = blog.get('title', '')

    if not content and not title:
        return jsonify({"success": False, "error": "Draft has no content to analyze."}), 400

    try:
        keywords = _extract_keywords_from_content(title, content)
    except Exception as e:
        current_app.logger.error(f"Keyword extraction failed: {e}")
        return jsonify({"success": False, "error": "Failed to extract keywords from content."}), 500

    if not keywords:
        return jsonify({"success": False, "error": "Could not extract keywords from this draft."}), 400

    api_key = current_app.config.get('AHREFS_RAPIDAPI_KEY')
    if not api_key:
        return jsonify({"success": False, "error": "API key not configured."}), 500

    results = []
    for kw in keywords:
        metrics = _fetch_keyword_metrics(kw, country, api_key)
        if metrics:
            results.append(metrics)
        else:
            results.append({"keyword": kw, "error": True})

    data = {"keywords": results, "blog_title": title}
    _cache.set(cache_key, data, ttl=CACHE_TTL)
    return jsonify({"success": True, "data": data, "cached": False})


def _extract_domain(url):
    """Extract clean domain from user input (strip protocol, path, etc.)."""
    if not url or not url.strip():
        return None
    url = url.strip().lower()
    if url.startswith(('http://', 'https://')):
        parsed = urlparse(url)
        domain = parsed.netloc
    else:
        domain = url.split('/')[0]
    domain = domain.replace('www.', '')
    if not domain or '.' not in domain:
        return None
    return domain


@optimization_bp.route('/api/optimization/site-audit')
@admin_required
def site_audit():
    domain_input = request.args.get('domain', '').strip()
    domain = _extract_domain(domain_input)
    if not domain:
        return jsonify({"success": False, "error": "Please enter a valid domain (e.g., example.com)."}), 400

    cache_key = f"site_audit:{domain}"
    cached = _cache.get(cache_key)
    if cached:
        return jsonify({"success": True, "data": cached, "cached": True})

    api_key = current_app.config.get('SITE_AUDIT_RAPIDAPI_KEY')
    if not api_key:
        return jsonify({"success": False, "error": "Site Audit API key not configured."}), 500

    try:
        response = requests.get(
            f"https://{SITE_AUDIT_HOST}/topsearchkeywords.php",
            params={"domain": domain},
            headers={
                "x-rapidapi-key": api_key,
                "x-rapidapi-host": SITE_AUDIT_HOST,
                "Content-Type": "application/json"
            },
            timeout=30
        )

        if response.status_code == 429:
            return jsonify({"success": False, "error": "API rate limit reached. Please try again later."}), 429

        if response.status_code == 403:
            return jsonify({"success": False, "error": "API access denied. Check your subscription."}), 403

        if response.status_code != 200:
            current_app.logger.warning(
                f"Site Audit API returned {response.status_code}: {response.text[:200]}"
            )
            return jsonify({"success": False, "error": f"API returned status {response.status_code}."}), 502

        raw = response.json()
        _cache.set(cache_key, raw, ttl=CACHE_TTL)
        return jsonify({"success": True, "data": raw, "cached": False})

    except requests.exceptions.Timeout:
        return jsonify({"success": False, "error": "Request timed out. The API may be temporarily slow — please try again."}), 504
    except requests.exceptions.ConnectionError:
        return jsonify({"success": False, "error": "Could not connect to the API. Check your network."}), 502
    except requests.exceptions.RequestException as e:
        current_app.logger.error(f"Site Audit API request failed: {e}")
        return jsonify({"success": False, "error": "Failed to connect to the API."}), 502
    except ValueError:
        return jsonify({"success": False, "error": "Invalid response from API."}), 502


@optimization_bp.route('/api/optimization/reports')
@admin_required
def get_reports():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"success": False, "error": "Not authenticated."}), 401
    reports = _db.get_user_seo_reports(user_id)
    return jsonify({"success": True, "reports": reports})


@optimization_bp.route('/api/optimization/reports/<report_id>', methods=['DELETE'])
@admin_required
def delete_report(report_id):
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"success": False, "error": "Not authenticated."}), 401
    deleted = _db.delete_seo_report(report_id, user_id)
    if deleted:
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Report not found or access denied."}), 404
