import os
import mailtrap as mt


class EmailService:
    """
    Email service using Mailtrap API.
    Sign up at https://mailtrap.io
    """

    def __init__(self):
        self.api_token = os.getenv("MAILTRAP_API_TOKEN")
        self.from_email = os.getenv("FROM_EMAIL", "noreply@scriptly.app")
        self.from_name = os.getenv("FROM_NAME", "Scriptly")
        self.inbox_id = int(os.getenv("MAILTRAP_INBOX_ID", "4651152"))

    def _get_client(self):
        """Get Mailtrap API client in sandbox mode."""
        if not self.api_token:
            return None
        return mt.MailtrapClient(token=self.api_token, sandbox=True, inbox_id=self.inbox_id)

    def send_single(self, to_email: str, subject: str, html_content: str):
        """Send email to a single recipient."""
        client = self._get_client()
        if not client:
            return {"success": False, "error": "MAILTRAP_API_TOKEN not configured"}

        try:
            mail = mt.Mail(
                sender=mt.Address(email=self.from_email, name=self.from_name),
                to=[mt.Address(email=to_email)],
                subject=subject,
                html=html_content,
            )
            response = client.send(mail)
            return {"success": True, "id": getattr(response, 'message_ids', [None])[0] if hasattr(response, 'message_ids') else str(response)}
        except Exception as e:
            print(f"Email send error: {e}")
            return {"success": False, "error": str(e)}

    def send_newsletter(self, to_emails: list, subject: str, html_content: str):
        """
        Send newsletter to multiple recipients.
        Uses individual sends for better deliverability and tracking.
        """
        client = self._get_client()
        if not client:
            return {"success": False, "error": "MAILTRAP_API_TOKEN not configured"}

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
                mail = mt.Mail(
                    sender=mt.Address(email=self.from_email, name=self.from_name),
                    to=[mt.Address(email=email)],
                    subject=subject,
                    html=html_content,
                )
                client.send(mail)
                results["sent"] += 1
            except Exception as e:
                results["failed"] += 1
                results["errors"].append({"email": email, "error": str(e)})

        if results["failed"] > 0 and results["sent"] == 0:
            results["success"] = False

        return results

    def send_batch(self, subscribers: list, subject: str, html_content: str,
                   base_url: str = "", site_name: str = "Newsletter"):
        """
        Send newsletter to all subscribers in batches.
        Personalizes unsubscribe link for each subscriber.

        Args:
            subscribers: List of subscriber dicts with 'email' key
            subject: Email subject line
            html_content: HTML content (can include {{ email }} for personalization)
            base_url: Base URL for unsubscribe links
            site_name: Name to show in footer
        """
        client = self._get_client()
        if not client:
            return {"success": False, "error": "MAILTRAP_API_TOKEN not configured"}

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

                mail = mt.Mail(
                    sender=mt.Address(email=self.from_email, name=self.from_name),
                    to=[mt.Address(email=email)],
                    subject=subject,
                    html=personalized_html,
                )
                client.send(mail)
                results["sent"] += 1

            except Exception as e:
                results["failed"] += 1
                results["errors"].append({"email": email, "error": str(e)})

        if results["sent"] == 0:
            results["success"] = False

        return results

    def test_connection(self):
        """Test if API token is valid."""
        if not self.api_token:
            return {"valid": False, "error": "MAILTRAP_API_TOKEN not set"}

        try:
            client = self._get_client()
            mail = mt.Mail(
                sender=mt.Address(email=self.from_email, name=self.from_name),
                to=[mt.Address(email=self.from_email)],
                subject="Connection Test",
                text="Test",
            )
            client.send(mail)
            return {"valid": True}
        except Exception as e:
            return {"valid": False, "error": str(e)}
