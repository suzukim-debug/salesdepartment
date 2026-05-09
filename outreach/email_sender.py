"""
メール送信 + 開封・クリックトラッキング
"""
import asyncio
import secrets
import json
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import aiosmtplib
from sqlalchemy.orm import Session
from database.models import Lead, OutreachLog, LeadStatus, ServiceType
from config import settings


def _generate_tracking_token() -> str:
    return secrets.token_urlsafe(32)


def _inject_tracking(body_html: str, token: str, lead_id: int) -> str:
    """HTMLメールに開封ピクセルとクリックトラッキングを埋め込む"""
    pixel_url = f"{settings.base_url}/track/open/{token}"
    tracking_pixel = f'<img src="{pixel_url}" width="1" height="1" style="display:none" />'

    # リンクにトラッキングURLを付与
    import re
    def replace_href(m):
        original_url = m.group(1)
        tracked_url = f"{settings.base_url}/track/click/{token}?url={original_url}"
        return f'href="{tracked_url}"'

    body_html = re.sub(r'href="(https?://[^"]+)"', replace_href, body_html)

    # 閉じbodyタグの前にピクセルを挿入（なければ末尾に追加）
    if "</body>" in body_html:
        body_html = body_html.replace("</body>", f"{tracking_pixel}</body>")
    else:
        body_html += tracking_pixel

    return body_html


def _build_html_email(subject: str, body_html: str, company_name: str) -> str:
    """フルHTMLメールテンプレートに本文を埋め込む"""
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{subject}</title>
<style>
  body {{ font-family: 'Hiragino Sans', 'Meiryo', sans-serif; font-size: 14px; color: #333; background: #f5f5f5; margin: 0; padding: 0; }}
  .wrapper {{ max-width: 600px; margin: 20px auto; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
  .header {{ background: #1a73e8; color: white; padding: 24px 32px; }}
  .header h1 {{ margin: 0; font-size: 18px; font-weight: normal; }}
  .body {{ padding: 32px; line-height: 1.8; }}
  .body p {{ margin: 0 0 16px 0; }}
  .cta {{ text-align: center; margin: 32px 0 16px; }}
  .cta a {{ background: #1a73e8; color: white; padding: 12px 32px; border-radius: 4px; text-decoration: none; font-weight: bold; display: inline-block; }}
  .footer {{ background: #f5f5f5; padding: 16px 32px; font-size: 12px; color: #888; text-align: center; }}
</style>
</head>
<body>
<div class="wrapper">
  <div class="header"><h1>{subject}</h1></div>
  <div class="body">
    {body_html}
    <div class="cta">
      <a href="{settings.base_url}/form/{{}}" style="color:white;">15分オンライン説明会に申し込む</a>
    </div>
  </div>
  <div class="footer">
    <p>このメールは営業目的でお送りしています。</p>
    <p><a href="{settings.base_url}/unsubscribe/{{}}" style="color:#888;">配信停止はこちら</a></p>
  </div>
</div>
</body>
</html>"""


async def send_email_async(
    to_email: str,
    subject: str,
    body_text: str,
    body_html: str,
) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{settings.smtp_from_name} <{settings.smtp_user}>"
    msg["To"] = to_email

    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    await aiosmtplib.send(
        msg,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_user,
        password=settings.smtp_password,
        start_tls=True,
    )


def create_outreach_log(
    db: Session,
    lead: Lead,
    subject: str,
    body_text: str,
    body_html: str,
    service_type: ServiceType,
) -> OutreachLog:
    token = _generate_tracking_token()
    tracked_html = _inject_tracking(body_html, token, lead.id)
    full_html = _build_html_email(subject, tracked_html, lead.company_name)
    # トークンをCTA・配信停止リンクに埋め込む
    full_html = full_html.replace("{}", token, 2)

    log = OutreachLog(
        lead_id=lead.id,
        service_type=service_type,
        subject=subject,
        body_html=full_html,
        body_text=body_text,
        tracking_token=token,
    )
    db.add(log)
    db.flush()
    return log


def send_outreach_email(
    db: Session,
    lead: Lead,
    subject: str,
    body_text: str,
    body_html: str,
    service_type: ServiceType,
) -> OutreachLog:
    """メールを送信してOutreachLogに記録する"""
    import smtplib

    if not lead.contact_email:
        raise ValueError(f"リード {lead.company_name} にメールアドレスがありません")

    log = create_outreach_log(db, lead, subject, body_text, body_html, service_type)

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{settings.smtp_from_name} <{settings.smtp_user}>"
        msg["To"] = lead.contact_email
        msg.attach(MIMEText(body_text, "plain", "utf-8"))
        msg.attach(MIMEText(log.body_html, "html", "utf-8"))

        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)
            server.sendmail(settings.smtp_user, [lead.contact_email], msg.as_bytes())

        log.sent_at = datetime.utcnow()
        lead.status = LeadStatus.EMAIL_SENT
    except Exception as e:
        log.error = str(e)
        raise

    db.flush()
    return log
