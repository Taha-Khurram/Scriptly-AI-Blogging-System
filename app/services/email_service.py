import hashlib
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from app.core.logging import get_logger
from app.utils.cache import cache

logger = get_logger(__name__)

# An unreachable or slow-to-answer mail server must not hold a worker thread
# open indefinitely. smtplib defaults to no timeout at all, which means a
# blackholed SMTP port blocks until the OS gives up -- minutes, on one of a
# small number of gthread workers.
SMTP_TIMEOUT_SECONDS = 10

# How long a successful credential check stays good. SMTP credentials change
# only when the environment changes or Google revokes the app password, and the
# cache key includes the credentials, so a change invalidates this on its own.
_STATUS_TTL_SECONDS = 600
# Failures are re-checked far sooner: a transient network problem should not
# leave the screen claiming email is misconfigured for ten minutes.
_STATUS_FAILURE_TTL_SECONDS = 30


class EmailService:
    """
    Email service using Gmail SMTP.
    No domain verification needed — sends to any email address.
    Requires a Gmail App Password (not your regular password).

    Setup:
    1. Enable 2-Step Verification on your Google Account
    2. Go to https://myaccount.google.com/apppasswords
    3. Generate an App Password for "Mail"
    4. Set GMAIL_APP_PASSWORD in .env
    """

    def __init__(self):
        self.smtp_host = "smtp.gmail.com"
        self.smtp_port = 587
        self.from_email = os.getenv("GMAIL_USER")
        self.from_name = os.getenv("FROM_NAME", "Scriptly")
        self.app_password = os.getenv("GMAIL_APP_PASSWORD")

    def _get_from_address(self):
        return f"{self.from_name} <{self.from_email}>"

    def _send_email(self, to_email: str, subject: str, html_content: str):
        """Send a single email via Gmail SMTP."""
        msg = MIMEMultipart("alternative")
        msg["From"] = self._get_from_address()
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(html_content, "html"))

        with smtplib.SMTP(self.smtp_host, self.smtp_port,
                          timeout=SMTP_TIMEOUT_SECONDS) as server:
            server.starttls()
            server.login(self.from_email, self.app_password)
            server.sendmail(self.from_email, to_email, msg.as_string())

    def send_single(self, to_email: str, subject: str, html_content: str):
        """Send email to a single recipient."""
        if not self.from_email or not self.app_password:
            return {"success": False, "error": "Gmail credentials not configured (GMAIL_USER / GMAIL_APP_PASSWORD)"}

        try:
            self._send_email(to_email, subject, html_content)
            return {"success": True, "id": f"sent-to-{to_email}"}
        except Exception as e:
            logger.exception("Email send error")
            return {"success": False, "error": str(e)}

    def send_newsletter(self, to_emails: list, subject: str, html_content: str):
        """Send newsletter to multiple recipients individually."""
        if not self.from_email or not self.app_password:
            return {"success": False, "error": "Gmail credentials not configured (GMAIL_USER / GMAIL_APP_PASSWORD)"}

        if not to_emails:
            return {"success": False, "error": "No recipients provided"}

        results = {
            "success": True,
            "sent": 0,
            "failed": 0,
            "errors": []
        }

        for email in to_emails:
            try:
                self._send_email(email, subject, html_content)
                results["sent"] += 1
            except Exception as e:
                results["failed"] += 1
                results["errors"].append({"email": email, "error": str(e)})

        if results["failed"] > 0 and results["sent"] == 0:
            results["success"] = False

        return results

    def send_batch(self, subscribers: list, subject: str, html_content: str,
                   base_url: str = "", site_name: str = "Newsletter"):
        """Send newsletter to all subscribers with personalized unsubscribe links."""
        if not self.from_email or not self.app_password:
            return {"success": False, "error": "Gmail credentials not configured (GMAIL_USER / GMAIL_APP_PASSWORD)"}

        if not subscribers:
            return {"success": False, "error": "No subscribers"}

        results = {
            "success": True,
            "total": len(subscribers),
            "sent": 0,
            "failed": 0,
            "errors": []
        }

        for subscriber in subscribers:
            email = subscriber.get('email')
            if not email:
                continue

            try:
                personalized_html = html_content.replace(
                    "{{ email }}", email
                ).replace(
                    "{{ unsubscribe_url }}",
                    f"{base_url}/unsubscribe?email={email}"
                )
                self._send_email(email, subject, personalized_html)
                results["sent"] += 1

            except Exception as e:
                results["failed"] += 1
                results["errors"].append({"email": email, "error": str(e)})

        if results["sent"] == 0:
            results["success"] = False

        return results

    def test_connection(self):
        """Whether the Gmail SMTP credentials are valid. Cached.

        This performs a TCP connect, a STARTTLS handshake and an SMTP AUTH --
        seconds of pure network round trips. It backs a status indicator on the
        newsletter screen, which called it on every page load and so paid a full
        SMTP login just to render a green dot; measured in the browser, that one
        endpoint took 2.58 s.

        The answer is a property of the credentials, not of the request, so it
        is cached against a digest of them. A credential change produces a
        different key and re-checks immediately, and a failure is cached only
        briefly so a blip does not persist as a false "misconfigured".
        """
        if not self.from_email or not self.app_password:
            return {"valid": False, "error": "GMAIL_USER or GMAIL_APP_PASSWORD not set"}

        # A digest, never the password itself: cache keys reach logs and
        # Redis's keyspace, and neither is a place for a credential.
        fingerprint = hashlib.sha256(
            ('%s\0%s' % (self.from_email, self.app_password)).encode()
        ).hexdigest()[:16]
        cache_key = 'smtp_status:%s' % fingerprint

        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port,
                              timeout=SMTP_TIMEOUT_SECONDS) as server:
                server.starttls()
                server.login(self.from_email, self.app_password)
            result = {"valid": True}
            ttl = _STATUS_TTL_SECONDS
        except Exception as e:
            result = {"valid": False, "error": str(e)}
            ttl = _STATUS_FAILURE_TTL_SECONDS

        cache.set(cache_key, result, ttl)
        return result
