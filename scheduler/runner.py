"""
APScheduler セットアップ
FastAPI の lifespan イベントで起動・停止する
"""
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from database.db import SessionLocal
from config import settings

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler(timezone="Asia/Tokyo")


def _with_db(func):
    """DB セッションを自動管理するラッパー"""
    def wrapper():
        db = SessionLocal()
        try:
            result = func(db)
            db.commit()
            return result
        except Exception as e:
            db.rollback()
            logger.error(f"{func.__name__} failed: {e}")
        finally:
            db.close()
    return wrapper


def _job_followup(db):
    from scheduler.follow_up import check_and_send_followups
    return check_and_send_followups(db)


def _job_gmail_check(db):
    from scheduler.follow_up import run_gmail_reply_check
    return run_gmail_reply_check(db)


def _job_sheets_export(db):
    from scheduler.follow_up import run_sheets_export
    return run_sheets_export(db)


def _job_scheduled_sends(db):
    from gmail_integration.sender import send_scheduled_emails
    return send_scheduled_emails(db)


def start_scheduler():
    """スケジューラを起動してジョブを登録する"""
    if scheduler.running:
        return

    # フォローアップメール: 毎朝9:00
    scheduler.add_job(
        _with_db(_job_followup),
        CronTrigger(hour=9, minute=0),
        id="follow_up",
        replace_existing=True,
        name="7日後フォローアップ送信",
    )

    # Gmail返信チェック: 15分間隔
    scheduler.add_job(
        _with_db(_job_gmail_check),
        IntervalTrigger(minutes=settings.gmail_check_interval_minutes),
        id="gmail_reply_check",
        replace_existing=True,
        name="Gmail返信チェック",
    )

    # Sheetsエクスポート: 毎朝8:30
    scheduler.add_job(
        _with_db(_job_sheets_export),
        CronTrigger(hour=8, minute=30),
        id="sheets_export",
        replace_existing=True,
        name="Sheetsエクスポート",
    )

    # スケジュール送信: 10分間隔
    scheduler.add_job(
        _with_db(_job_scheduled_sends),
        IntervalTrigger(minutes=10),
        id="scheduled_sends",
        replace_existing=True,
        name="スケジュール送信実行",
    )

    scheduler.start()
    logger.info("スケジューラ起動完了")


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown()
        logger.info("スケジューラ停止")


def get_scheduler_status() -> list[dict]:
    """ジョブ一覧と次回実行時刻を返す"""
    if not scheduler.running:
        return []
    return [
        {
            "id": job.id,
            "name": job.name,
            "next_run": job.next_run_time.strftime("%Y/%m/%d %H:%M") if job.next_run_time else "未定",
        }
        for job in scheduler.get_jobs()
    ]
