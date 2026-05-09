"""
日常オペレーション用CLIツール
使い方: python -m cli_tools.cli [コマンド]
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import click
from rich.console import Console
from rich.table import Table
from rich.progress import track
from sqlalchemy import desc

from database.db import init_db, get_db_session
from database.models import Lead, LeadStatus, ServiceType
from lead_generator.csv_importer import import_from_csv
from lead_generator.scorer import batch_score_leads
from outreach.template_generator import generate_email
from outreach.email_sender import send_outreach_email

console = Console()


@click.group()
def cli():
    """Sales Outreach System CLI"""
    init_db()


@cli.command()
@click.argument("csv_path")
@click.option("--no-skip", is_flag=True, help="重複チェックをスキップ")
def import_leads(csv_path, no_skip):
    """CSVからリードをインポートする"""
    with get_db_session() as db:
        result = import_from_csv(csv_path, db, skip_duplicates=not no_skip)
    console.print(f"[green]✓ 取込: {result['imported']}件 / スキップ: {result['skipped']}件[/green]")
    for err in result["errors"]:
        console.print(f"[red]  エラー: {err}[/red]")


@cli.command()
@click.option("--min-score", default=0.0, help="最低スコア")
@click.option("--status", default=None, help="ステータスで絞り込み")
@click.option("--limit", default=20, help="表示件数")
def list_leads(min_score, status, limit):
    """リード一覧を表示する"""
    with get_db_session() as db:
        q = db.query(Lead)
        if min_score > 0:
            q = q.filter(Lead.lead_score >= min_score)
        if status:
            q = q.filter(Lead.status == LeadStatus(status))
        leads = q.order_by(desc(Lead.lead_score)).limit(limit).all()

        table = Table(title=f"リード一覧 (上位{limit}件)")
        table.add_column("ID", style="dim")
        table.add_column("会社名", style="bold")
        table.add_column("業種")
        table.add_column("スコア", justify="right")
        table.add_column("推奨サービス")
        table.add_column("ステータス")
        table.add_column("メール")

        for lead in leads:
            score_str = f"[green]{lead.lead_score}[/green]" if lead.lead_score >= 70 \
                else f"[yellow]{lead.lead_score}[/yellow]" if lead.lead_score >= 40 \
                else f"[red]{lead.lead_score}[/red]"
            table.add_row(
                str(lead.id),
                lead.company_name,
                lead.industry or "-",
                score_str,
                lead.recommended_service.value if lead.recommended_service else "-",
                lead.status.value,
                "✓" if lead.contact_email else "✗",
            )
        console.print(table)


@cli.command()
def hot_leads():
    """ホットリード（反響あり）を表示する"""
    with get_db_session() as db:
        leads = (
            db.query(Lead)
            .filter(Lead.status.in_([LeadStatus.RESPONDED, LeadStatus.CLICKED]))
            .order_by(desc(Lead.lead_score))
            .all()
        )
        if not leads:
            console.print("[yellow]ホットリードはありません[/yellow]")
            return

        table = Table(title="🔥 ホットリード", border_style="red")
        table.add_column("会社名", style="bold red")
        table.add_column("業種")
        table.add_column("スコア", justify="right")
        table.add_column("電話番号")
        table.add_column("担当者")
        table.add_column("ステータス")

        for lead in leads:
            table.add_row(
                lead.company_name,
                lead.industry or "-",
                str(lead.lead_score),
                lead.contact_phone or lead.company_phone or "-",
                f"{lead.contact_name or ''} {lead.contact_title or ''}".strip() or "-",
                lead.status.value,
            )
        console.print(table)


@cli.command()
@click.option("--min-score", default=50.0, help="対象の最低スコア (デフォルト: 50)")
@click.option("--limit", default=10, help="送信件数上限")
@click.option("--dry-run", is_flag=True, help="実際には送信しない（確認用）")
def send_outreach(min_score, limit, dry_run):
    """対象リードにAIカスタマイズメールを一括送信する"""
    with get_db_session() as db:
        targets = (
            db.query(Lead)
            .filter(
                Lead.status.in_([LeadStatus.NEW, LeadStatus.RESEARCHED]),
                Lead.lead_score >= min_score,
                Lead.contact_email.isnot(None),
            )
            .order_by(desc(Lead.lead_score))
            .limit(limit)
            .all()
        )

        if not targets:
            console.print("[yellow]送信対象がありません[/yellow]")
            return

        console.print(f"[bold]送信対象: {len(targets)}社[/bold]")
        for lead in targets:
            console.print(f"  • {lead.company_name} (スコア: {lead.lead_score})")

        if dry_run:
            console.print("[yellow]--dry-run モード: 実際には送信しません[/yellow]")
            return

        if not click.confirm("\n本当に送信しますか？"):
            return

        success = 0
        fail = 0
        for lead in track(targets, description="メール送信中..."):
            try:
                email = generate_email(lead)
                send_outreach_email(
                    db, lead,
                    email["subject"], email["body"], email["body_html"],
                    ServiceType(email["service_type"]),
                )
                success += 1
            except Exception as e:
                console.print(f"[red]  ✗ {lead.company_name}: {e}[/red]")
                fail += 1

        console.print(f"\n[green]送信完了: {success}件成功 / {fail}件失敗[/green]")


@cli.command()
@click.argument("lead_id", type=int)
def preview_email(lead_id):
    """指定リードのAIメールをプレビューする"""
    with get_db_session() as db:
        lead = db.query(Lead).filter(Lead.id == lead_id).first()
        if not lead:
            console.print(f"[red]ID {lead_id} のリードが見つかりません[/red]")
            return

        console.print(f"\n[bold]会社: {lead.company_name}[/bold]")
        console.print(f"スコア: {lead.lead_score} / 推奨: {lead.recommended_service}")
        console.print("\n生成中...")

        email = generate_email(lead)
        console.print(f"\n[bold cyan]件名:[/bold cyan] {email['subject']}")
        console.print(f"\n[bold cyan]本文:[/bold cyan]\n{email['body']}")


@cli.command()
def rescore():
    """全リードを再スコアリングする"""
    with get_db_session() as db:
        leads = db.query(Lead).all()
        batch_score_leads(leads)
        console.print(f"[green]✓ {len(leads)}件を再スコアリングしました[/green]")


if __name__ == "__main__":
    cli()
