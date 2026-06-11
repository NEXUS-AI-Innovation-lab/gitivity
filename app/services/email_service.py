"""Async SMTP email sender for the approval workflow."""
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib

from app.config.settings import settings

logger = logging.getLogger(__name__)


class EmailService:
    """Sends HTML emails through the configured SMTP server (Gmail by default)."""

    async def send_html(self, to: str, subject: str, html: str) -> None:
        """Send an HTML email.

        Raises:
            RuntimeError: If SMTP credentials are not configured.
            aiosmtplib.SMTPException: On SMTP failure.
        """
        if not settings.SMTP_USER or not settings.SMTP_PASSWORD:
            raise RuntimeError(
                "SMTP_USER / SMTP_PASSWORD not configured - cannot send email"
            )

        message = MIMEMultipart("alternative")
        message["From"] = settings.SMTP_FROM or settings.SMTP_USER
        message["To"] = to
        message["Subject"] = subject
        message.attach(MIMEText(html, "html", "utf-8"))

        await aiosmtplib.send(
            message,
            hostname=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            username=settings.SMTP_USER,
            password=settings.SMTP_PASSWORD,
            start_tls=settings.SMTP_STARTTLS,
            timeout=settings.SMTP_TIMEOUT,
        )
        logger.info(f"Email sent to {to}: {subject}")


email_service = EmailService()
