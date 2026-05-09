"""
メール送信テスト
python test_email.py で実行
"""
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from config import settings

def test_smtp():
    if not settings.smtp_password:
        print("❌ .env に SMTP_PASSWORD が設定されていません")
        print()
        print("【アプリパスワードの取得方法】")
        print("1. https://myaccount.google.com/security を開く")
        print("2. 「2段階認証プロセス」が有効になっているか確認")
        print("3. 「アプリパスワード」→「メール」「その他のデバイス」で16文字のパスワード生成")
        print("4. .env の SMTP_PASSWORD= に貼り付け")
        print()
        print("※ 2段階認証が無効の場合: Google Workspace管理者に有効化を依頼するか")
        print("   Gmail API (OAuth2) 方式に切り替えが必要です")
        sys.exit(1)

    to_addr = settings.smtp_user  # 自分自身に送信
    subject = "【テスト】営業システムからの送信確認"
    body_text = """
鈴木様

これはシステムからの送信テストです。

このメールが届いていれば、営業メール自動送信システムが正常に動作しています。
リード数・SNS診断・メール生成・送信まで一気通貫で動きます。

---
salesdepartment 営業自動化システム
""".strip()

    body_html = f"""
<html><body>
<p>鈴木様</p>
<p>これはシステムからの送信テストです。</p>
<p>このメールが届いていれば、営業メール自動送信システムが正常に動作しています。<br>
リード数・SNS診断・メール生成・送信まで一気通貫で動きます。</p>
<hr>
<small>salesdepartment 営業自動化システム</small>
</body></html>
"""

    print(f"送信中: {settings.smtp_user} → {to_addr}")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{settings.smtp_from_name} <{settings.smtp_user}>"
    msg["To"] = to_addr
    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)
            server.sendmail(settings.smtp_user, [to_addr], msg.as_bytes())
        print(f"✅ 送信成功！ {to_addr} を確認してください")
    except smtplib.SMTPAuthenticationError:
        print("❌ 認証エラー: アプリパスワードが間違っているか、2段階認証が未設定です")
        print("   https://myaccount.google.com/apppasswords でパスワードを再発行してください")
    except Exception as e:
        print(f"❌ エラー: {e}")

if __name__ == "__main__":
    test_smtp()
