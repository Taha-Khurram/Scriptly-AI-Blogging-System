import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from app.core.logging import get_logger

logger = get_logger(__name__)


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

        with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
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
        """Test if Gmail SMTP credentials are valid."""
        if not self.from_email or not self.app_password:
            return {"valid": False, "error": "GMAIL_USER or GMAIL_APP_PASSWORD not set"}

        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.login(self.from_email, self.app_password)
            return {"valid": True}
        except Exception as e:
            return {"valid": False, "error": str(e)}
