"""Email notifications for Vaktin.

Sends email when critical or important nature conservation items are found.
Uses Gmail SMTP with an app password (stored as GMAIL_APP_PASSWORD secret).

Requires two environment variables:
  GMAIL_SENDER   — the Gmail address to send from
  GMAIL_APP_PASSWORD — a Google App Password (NOT the regular password)
"""

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

RECIPIENTS_PATH = Path(__file__).parent.parent / "config" / "recipients.yml"

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

# Severity emoji for subject line
SEVERITY_ICONS = {
    "critical": "\U0001f534",   # red circle
    "important": "\U0001f7e1",  # yellow circle
    "monitor": "\U0001f535",    # blue circle
}


def _load_recipients() -> dict:
    """Load recipient lists from config/recipients.yml."""
    if not RECIPIENTS_PATH.exists():
        logger.warning("No recipients.yml found — skipping email")
        return {}
    with open(RECIPIENTS_PATH) as f:
        return yaml.safe_load(f) or {}


def _build_email_body(results: list[dict]) -> str:
    """Build an Icelandic HTML email body from analysis results."""
    critical = [r for r in results if r.get("severity") == "critical"]
    important = [r for r in results if r.get("severity") == "important"]

    parts = []
    parts.append("<h2>Vaktin &mdash; ný mál fundust</h2>")

    if critical:
        parts.append(f"<h3>{SEVERITY_ICONS['critical']} Mikilvæg mál ({len(critical)})</h3>")
        parts.append(_render_items(critical))

    if important:
        parts.append(f"<h3>{SEVERITY_ICONS['important']} Athyglisverð mál ({len(important)})</h3>")
        parts.append(_render_items(important))

    parts.append("<hr>")
    parts.append(
        '<p style="color:#888;font-size:12px;">'
        'Sent sjálfvirkt af <a href="https://sunnuhvoll.github.io/vaktin/">Vaktin</a>'
        "</p>"
    )

    return "\n".join(parts)


def _render_items(items: list[dict]) -> str:
    """Render a list of items as an HTML list."""
    rows = []
    for item in items:
        title = item.get("title", "Ótitlað")
        url = item.get("url", "")
        summary = item.get("summary_is", "")
        category = item.get("category", "")
        source = item.get("source_id", "")

        link = f'<a href="{url}">{title}</a>' if url else title
        meta = " &mdash; ".join(filter(None, [source, category]))

        rows.append(
            f"<li><strong>{link}</strong>"
            f"<br>{summary}"
            f'<br><span style="color:#888;font-size:12px;">{meta}</span></li>'
        )

    return "<ul>" + "\n".join(rows) + "</ul>"


def _build_subject(results: list[dict]) -> str:
    """Build email subject line."""
    critical = sum(1 for r in results if r.get("severity") == "critical")
    important = sum(1 for r in results if r.get("severity") == "important")

    parts = []
    if critical:
        parts.append(f"{critical} mikilvæg")
    if important:
        parts.append(f"{important} athyglisverð")

    return f"Vaktin: {', '.join(parts)} mál fundust"


def send_notification(results: list[dict]) -> None:
    """Send email notification if there are critical or important results.

    Does nothing if:
    - No critical/important results
    - GMAIL_SENDER or GMAIL_APP_PASSWORD not set
    - No recipients configured
    """
    # Filter to critical + important only
    notify_results = [
        r for r in results
        if r.get("severity") in ("critical", "important")
    ]
    if not notify_results:
        logger.info("No critical/important items — skipping email notification")
        return

    sender = os.environ.get("GMAIL_SENDER", "")
    password = os.environ.get("GMAIL_APP_PASSWORD", "")
    if not sender or not password:
        logger.info("GMAIL_SENDER/GMAIL_APP_PASSWORD not set — skipping email")
        return

    recipients_config = _load_recipients()
    if not recipients_config:
        return

    # Build recipient list
    all_recipients = set(recipients_config.get("critical_and_important", []) or [])
    critical_only = set(recipients_config.get("critical_only", []) or [])

    critical_results = [r for r in notify_results if r.get("severity") == "critical"]
    has_critical = len(critical_results) > 0

    to_addrs = list(all_recipients)
    if has_critical:
        to_addrs.extend(critical_only)

    if not to_addrs:
        logger.info("No email recipients configured — skipping notification")
        return

    # Build and send email
    subject = _build_subject(notify_results)
    body = _build_email_body(notify_results)

    msg = MIMEMultipart("alternative")
    msg["From"] = sender
    msg["To"] = ", ".join(to_addrs)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "html", "utf-8"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(sender, password)
            server.sendmail(sender, to_addrs, msg.as_string())
        logger.info(f"Email sent to {len(to_addrs)} recipients: {subject}")
    except Exception as e:
        logger.error(f"Failed to send email notification: {e}")
