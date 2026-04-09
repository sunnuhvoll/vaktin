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

SEVERITY_COLORS = {
    "critical": {"bg": "#fef2f2", "border": "#dc2626", "badge_bg": "#dc2626", "badge_text": "#ffffff", "label": "Aðkallandi"},
    "important": {"bg": "#fffbeb", "border": "#d97706", "badge_bg": "#d97706", "badge_text": "#ffffff", "label": "Mikilvægt"},
    "monitor": {"bg": "#eff6ff", "border": "#2563eb", "badge_bg": "#2563eb", "badge_text": "#ffffff", "label": "Til eftirlits"},
}


def _load_recipients() -> dict:
    """Load recipient lists from config/recipients.yml."""
    if not RECIPIENTS_PATH.exists():
        logger.warning("No recipients.yml found — skipping email")
        return {}
    with open(RECIPIENTS_PATH) as f:
        return yaml.safe_load(f) or {}


def _render_item(item: dict) -> str:
    """Render a single item as an HTML card."""
    severity = item.get("severity", "monitor")
    colors = SEVERITY_COLORS.get(severity, SEVERITY_COLORS["monitor"])

    dek = item.get("dek_is", "")
    summary = item.get("summary_is", "")
    url = item.get("url", "")
    source = item.get("source_id", "")
    categories = item.get("categories", [])
    deadline = item.get("deadline", "")
    location = item.get("location", "")

    # Category tags
    cat_html = ""
    if categories:
        tags = "".join(
            f'<span style="display:inline-block;background:#f1f5f9;color:#475569;'
            f'font-size:11px;padding:2px 8px;border-radius:10px;margin-right:4px;'
            f'margin-bottom:4px;">{cat}</span>'
            for cat in categories[:5]
        )
        cat_html = f'<div style="margin-top:10px;">{tags}</div>'

    # Metadata line (source, deadline, location)
    meta_parts = []
    if source:
        meta_parts.append(source)
    if location:
        meta_parts.append(f"📍 {location}")
    if deadline:
        meta_parts.append(f"⏰ Frestur: {deadline}")
    meta_html = ""
    if meta_parts:
        meta_html = (
            f'<div style="margin-top:10px;font-size:12px;color:#94a3b8;">'
            f'{" &nbsp;·&nbsp; ".join(meta_parts)}</div>'
        )

    # Links
    item_id = item.get("item_id", "")
    vaktin_url = f"https://sunnuhvoll.github.io/vaktin/reports/#{item_id}" if item_id else ""

    link_parts = []
    if vaktin_url:
        link_parts.append(
            f'<a href="{vaktin_url}" style="color:{colors["border"]};font-size:13px;'
            f'text-decoration:none;font-weight:bold;">Skoða á Vaktin →</a>'
        )
    if url:
        link_parts.append(
            f'<a href="{url}" style="color:#64748b;font-size:13px;'
            f'text-decoration:none;">Upprunalegt efni ↗</a>'
        )
    link_html = ""
    if link_parts:
        link_html = f'<div style="margin-top:12px;">{" &nbsp;·&nbsp; ".join(link_parts)}</div>'

    return f"""
    <div style="background:{colors['bg']};border-left:4px solid {colors['border']};
                border-radius:8px;padding:20px;margin-bottom:16px;">
      <div style="margin-bottom:8px;">
        <span style="display:inline-block;background:{colors['badge_bg']};color:{colors['badge_text']};
                     font-size:11px;font-weight:bold;padding:3px 10px;border-radius:12px;
                     text-transform:uppercase;letter-spacing:0.5px;">
          {colors['label']}
        </span>
      </div>
      <div style="font-size:17px;font-weight:bold;color:#1e293b;line-height:1.4;margin-bottom:6px;">
        {dek}
      </div>
      <div style="font-size:14px;color:#475569;line-height:1.6;">
        {summary}
      </div>
      {cat_html}
      {meta_html}
      {link_html}
    </div>
    """


def _build_email_body(results: list[dict]) -> str:
    """Build a styled HTML email body from analysis results."""
    critical = [r for r in results if r.get("severity") == "critical"]
    important = [r for r in results if r.get("severity") == "important"]

    items_html = ""
    for item in critical + important:
        items_html += _render_item(item)

    count = len(critical) + len(important)

    return f"""
    <div style="font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;max-width:640px;
                margin:0 auto;background:#ffffff;">
      <!-- Header -->
      <div style="background:linear-gradient(135deg,#0f172a 0%,#1e3a5f 100%);
                  padding:28px 24px;border-radius:12px 12px 0 0;">
        <h1 style="color:#ffffff;font-size:22px;font-weight:700;margin:0;">
          🔭 Vaktin
        </h1>
        <p style="color:#94a3b8;font-size:14px;margin:6px 0 0 0;">
          {count} {'nýtt mál fundist' if count == 1 else 'ný mál fundust'} sem krefjast athygli
        </p>
      </div>

      <!-- Body -->
      <div style="padding:24px;">
        {items_html}
      </div>

      <!-- Footer -->
      <div style="padding:16px 24px;border-top:1px solid #e2e8f0;text-align:center;">
        <a href="https://sunnuhvoll.github.io/vaktin/"
           style="color:#64748b;font-size:12px;text-decoration:none;">
          Skoða öll mál á Vaktin vefsíðu →
        </a>
        <p style="color:#94a3b8;font-size:11px;margin:8px 0 0 0;">
          Sent sjálfvirkt af Vaktin — náttúruverndareftirliti
        </p>
      </div>
    </div>
    """


def _build_subject(results: list[dict]) -> str:
    """Build email subject line."""
    critical = [r for r in results if r.get("severity") == "critical"]
    important = [r for r in results if r.get("severity") == "important"]

    # Use the dek_is of the most important item as subject hint
    top_item = (critical or important or [{}])[0]
    dek = top_item.get("dek_is", "")

    parts = []
    if critical:
        parts.append(f"🔴 {len(critical)} aðkallandi")
    if important:
        parts.append(f"🟡 {len(important)} mikilvæg")

    subject = f"Vaktin: {', '.join(parts)}"
    if dek and len(subject) + len(dek) < 120:
        subject += f" — {dek}"

    return subject


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
    msg["From"] = f"Vaktin <{sender}>"
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
