"""
FastAPI ダッシュボード (全8ステップ対応版)
① スクレイピング制御
② AI分析・スコアリング確認
③ メール生成（Claude API）
④ Gmail送信・スケジュール
⑤ 反響検知（開封/クリック/返信/フォーム）
⑥ テレアポリスト（Sheetsエクスポート）
⑦ テレアポ架電管理
⑧ 結果記録・分析
"""
import json
import io
import logging
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Depends, Request, Form, UploadFile, File, HTTPException, Query, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, Response, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import desc, func

from database.db import get_db, init_db
from database.models import (
    Lead, OutreachLog, CallLog, FollowUpLog, ScrapeJob,
    LeadStatus, ServiceType, ScrapeStatus, SchedulerJob
)
from lead_generator.csv_importer import import_from_csv, get_csv_template
from lead_generator.scorer import score_lead, batch_score_leads
from outreach.template_generator import generate_email
from outreach.email_sender import create_outreach_log
from tracker.response_tracker import (
    record_open, record_click, record_form_submit, record_unsubscribe
)
from config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from scheduler.runner import start_scheduler, stop_scheduler
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Sales Outreach System", lifespan=lifespan)
templates = Jinja2Templates(directory="dashboard/templates")


# ─── ユーティリティ ─────────────────────────────────────────

def _get_stats(db: Session) -> dict:
    total = db.query(func.count(Lead.id)).scalar()
    hot = db.query(func.count(Lead.id)).filter(
        Lead.status.in_([LeadStatus.RESPONDED, LeadStatus.REPLIED])
    ).scalar()
    sent = db.query(func.count(OutreachLog.id)).filter(OutreachLog.sent_at.isnot(None)).scalar()
    meetings = db.query(func.count(Lead.id)).filter(Lead.status == LeadStatus.MEETING_SET).scalar()
    calls = db.query(func.count(CallLog.id)).scalar()
    open_rate = 0
    if sent > 0:
        opened = db.query(func.count(OutreachLog.id)).filter(OutreachLog.opened_at.isnot(None)).scalar()
        open_rate = round(opened / sent * 100, 1)
    return {
        "total": total, "hot": hot, "sent": sent,
        "meetings": meetings, "calls": calls, "open_rate": open_rate,
    }


# ─── ダッシュボード TOP ─────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    stats = _get_stats(db)
    hot_leads = (
        db.query(Lead)
        .filter(Lead.status.in_([LeadStatus.RESPONDED, LeadStatus.REPLIED, LeadStatus.CLICKED]))
        .order_by(desc(Lead.lead_score))
        .limit(10).all()
    )
    from scheduler.runner import get_scheduler_status
    scheduler_jobs = get_scheduler_status()
    followup_pending = db.query(func.count(Lead.id)).filter(
        Lead.status.in_([LeadStatus.EMAIL_SENT, LeadStatus.OPENED])
    ).scalar()
    return templates.TemplateResponse("index.html", {
        "request": request, "stats": stats, "hot_leads": hot_leads,
        "scheduler_jobs": scheduler_jobs, "followup_pending": followup_pending,
        "now": datetime.utcnow(),
    })


# ─── ① スクレイピング ────────────────────────────────────────

@app.get("/scraper", response_class=HTMLResponse)
async def scraper_page(request: Request, db: Session = Depends(get_db)):
    pending = db.query(func.count(Lead.id)).filter(
        Lead.scrape_status == ScrapeStatus.PENDING, Lead.website.isnot(None)
    ).scalar()
    no_url = db.query(func.count(Lead.id)).filter(
        Lead.scrape_status == ScrapeStatus.PENDING, Lead.website.is_(None)
    ).scalar()
    done = db.query(func.count(Lead.id)).filter(Lead.scrape_status == ScrapeStatus.DONE).scalar()
    failed = db.query(func.count(Lead.id)).filter(Lead.scrape_status == ScrapeStatus.FAILED).scalar()
    recent_jobs = (
        db.query(ScrapeJob).order_by(desc(ScrapeJob.completed_at)).limit(20).all()
    )
    return templates.TemplateResponse("scraper.html", {
        "request": request,
        "pending": pending, "no_url": no_url, "done": done, "failed": failed,
        "recent_jobs": recent_jobs, "settings": settings,
    })


@app.post("/scraper/run")
async def run_scraper(
    background_tasks: BackgroundTasks,
    limit: int = Form(10),
    run_sns_deep: str = Form("1"),
    run_sns_diagnosis: str = Form("1"),
    db: Session = Depends(get_db),
):
    """バックグラウンドでスクレイピングを実行"""
    do_sns_deep = run_sns_deep == "1"
    do_diagnosis = run_sns_diagnosis == "1"

    def _run():
        from database.db import SessionLocal
        from scraper.orchestrator import run_batch_scrape
        session = SessionLocal()
        try:
            run_batch_scrape(
                session, limit=limit,
                run_sns_deep=do_sns_deep,
                run_sns_diagnosis=do_diagnosis,
            )
        finally:
            session.close()

    # 実際に処理できるリード数を事前確認
    candidate_count = db.query(func.count(Lead.id)).filter(
        Lead.scrape_status == ScrapeStatus.PENDING,
        Lead.website.isnot(None),
    ).scalar()
    no_url_count = db.query(func.count(Lead.id)).filter(
        Lead.scrape_status == ScrapeStatus.PENDING,
        Lead.website.is_(None),
    ).scalar()

    if candidate_count == 0:
        msg = f"⚠️ 対象リードが0件です。URLが登録されているリードがありません（URLなし: {no_url_count}件）"
        return JSONResponse({"message": msg})

    opts = []
    if do_sns_deep:
        opts.append("SNS深掘り")
    if do_diagnosis:
        opts.append("Claude診断")
    opt_str = f"（{'/'.join(opts)}）" if opts else ""
    actual = min(limit, candidate_count)
    background_tasks.add_task(_run)
    return JSONResponse({"message": f"✅ スクレイピング開始: {actual}件処理します{opt_str}（URL無しでスキップ: {no_url_count}件）"})


@app.post("/scraper/lead/{lead_id}")
async def scrape_single(
    lead_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """1社だけスクレイピング"""
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(404)

    def _run():
        from database.db import SessionLocal
        from scraper.orchestrator import run_scrape_for_lead
        session = SessionLocal()
        try:
            lead_obj = session.query(Lead).filter(Lead.id == lead_id).first()
            run_scrape_for_lead(lead_obj, session)
            session.commit()
        finally:
            session.close()

    background_tasks.add_task(_run)
    return JSONResponse({"message": f"{lead.company_name} のスクレイピングを開始"})


# ─── リード管理 ─────────────────────────────────────────────

@app.get("/leads", response_class=HTMLResponse)
async def leads_list(
    request: Request,
    status: Optional[str] = None,
    industry: Optional[str] = None,
    min_score: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    db: Session = Depends(get_db),
):
    min_score_val = float(min_score) if min_score else None
    q = db.query(Lead)
    if status:
        q = q.filter(Lead.status == status)
    if industry:
        q = q.filter(Lead.industry.contains(industry))
    if min_score_val:
        q = q.filter(Lead.lead_score >= min_score_val)
    if search:
        q = q.filter(Lead.company_name.contains(search))

    total_count = q.count()
    per_page = 30
    leads = q.order_by(desc(Lead.lead_score)).offset((page - 1) * per_page).limit(per_page).all()

    return templates.TemplateResponse("leads.html", {
        "request": request, "leads": leads,
        "total_count": total_count, "page": page, "per_page": per_page,
        "current_status": status, "current_industry": industry,
        "min_score": min_score, "search": search,
        "statuses": [s.value for s in LeadStatus],
    })


@app.get("/leads/{lead_id}", response_class=HTMLResponse)
async def lead_detail(request: Request, lead_id: int, db: Session = Depends(get_db)):
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(404)
    return templates.TemplateResponse("lead_detail.html", {
        "request": request, "lead": lead,
        "statuses": [s.value for s in LeadStatus],
    })


@app.get("/leads/{lead_id}/sns", response_class=HTMLResponse)
async def sns_diagnosis_page(request: Request, lead_id: int, db: Session = Depends(get_db)):
    """SNS診断レポートページ"""
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(404)
    diagnosis = lead.sns_diagnosis or {}
    sns_raw = lead.sns_raw_data or {}
    diagnosed_at = (
        lead.sns_analyzed_at.strftime("%Y/%m/%d %H:%M") if lead.sns_analyzed_at else "未実施"
    )
    return templates.TemplateResponse("sns_diagnosis.html", {
        "request": request, "lead": lead,
        "diagnosis": diagnosis if diagnosis else None,
        "sns_raw": sns_raw,
        "diagnosed_at": diagnosed_at,
    })


@app.post("/leads/{lead_id}/sns-diagnosis")
async def run_sns_diagnosis(
    lead_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """SNS深掘り分析 + Claude診断を実行（バックグラウンド）"""
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(404)

    def _run():
        from database.db import SessionLocal
        from scraper.orchestrator import run_sns_analysis_only
        session = SessionLocal()
        try:
            lead_obj = session.query(Lead).filter(Lead.id == lead_id).first()
            run_sns_analysis_only(lead_obj, session)
            session.commit()
        except Exception as e:
            session.rollback()
            import logging
            logging.getLogger(__name__).error(f"SNS diagnosis bg error: {e}")
        finally:
            session.close()

    background_tasks.add_task(_run)
    return JSONResponse({"success": True, "message": "SNS診断を開始しました"})


@app.post("/leads/{lead_id}/status")
async def update_lead_status(
    lead_id: int, status: str = Form(...), memo: str = Form(""),
    db: Session = Depends(get_db),
):
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(404)
    lead.status = LeadStatus(status)
    if memo:
        lead.memo = memo
    db.commit()
    return RedirectResponse(f"/leads/{lead_id}", status_code=303)


@app.post("/leads/import")
async def import_leads(file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = await file.read()
    result = import_from_csv(io.BytesIO(content), db)
    db.commit()
    return JSONResponse(result)


@app.post("/leads/import-exclude")
async def import_exclude_list(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """既存取引先リストをインポートして除外フラグを立てる"""
    import io as _io
    content = await file.read()
    try:
        import pandas as pd
        df = pd.read_csv(_io.BytesIO(content), encoding="utf-8-sig")
    except UnicodeDecodeError:
        df = pd.read_csv(_io.BytesIO(content), encoding="shift-jis")

    df.columns = [c.strip() for c in df.columns]
    marked = 0
    created = 0

    for _, row in df.iterrows():
        name_col = next((c for c in df.columns if c in ("会社名", "company_name")), None)
        if not name_col:
            break
        company_name = str(row.get(name_col, "")).strip()
        if not company_name:
            continue

        existing = db.query(Lead).filter(Lead.company_name == company_name).first()
        if existing:
            existing.status = LeadStatus.EXCLUDED
            marked += 1
        else:
            lead = Lead(
                company_name=company_name,
                status=LeadStatus.EXCLUDED,
                source="exclude_list",
            )
            db.add(lead)
            created += 1

    db.commit()
    return JSONResponse({"marked_excluded": marked, "created_excluded": created})


@app.get("/leads/export/template")
async def download_template():
    return Response(
        content=get_csv_template().encode("utf-8-sig"),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=lead_template.csv"},
    )


# ─── ③ メール生成 / ④ 送信 ─────────────────────────────────

@app.get("/outreach", response_class=HTMLResponse)
async def outreach_page(request: Request, db: Session = Depends(get_db)):
    targets = (
        db.query(Lead)
        .filter(
            Lead.status.in_([LeadStatus.NEW, LeadStatus.RESEARCHED]),
            Lead.status != LeadStatus.EXCLUDED,
            Lead.lead_score >= 20,
        )
        .order_by(desc(Lead.lead_score))
        .all()
    )
    sent_logs = (
        db.query(OutreachLog).order_by(desc(OutreachLog.sent_at)).limit(20).all()
    )
    from gmail_integration.auth import is_gmail_configured
    gmail_ok = is_gmail_configured()
    return templates.TemplateResponse("outreach.html", {
        "request": request, "targets": targets,
        "sent_logs": sent_logs, "gmail_ok": gmail_ok,
    })


@app.post("/outreach/preview")
async def preview_email(lead_id: int = Form(...), db: Session = Depends(get_db)):
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(404)
    try:
        return JSONResponse(generate_email(lead))
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/outreach/send")
async def send_email(
    lead_id: int = Form(...),
    subject: str = Form(...),
    body: str = Form(...),
    body_html: str = Form(...),
    service_type: str = Form(...),
    scheduled_at: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(404)

    stype = ServiceType(service_type)
    scheduled_dt = None
    if scheduled_at:
        try:
            scheduled_dt = datetime.fromisoformat(scheduled_at)
        except ValueError:
            pass

    try:
        from gmail_integration.sender import send_outreach_via_gmail
        send_outreach_via_gmail(db, lead, subject, body, body_html, stype, scheduled_dt)
        db.commit()
        msg = f"スケジュール登録: {scheduled_dt}" if scheduled_dt else "送信完了"
        return JSONResponse({"success": True, "message": msg})
    except Exception as e:
        db.rollback()
        raise HTTPException(500, str(e))


@app.post("/outreach/batch-send")
async def batch_send(
    background_tasks: BackgroundTasks,
    limit: int = Form(10),
    db: Session = Depends(get_db),
):
    """スコア上位N件を自動でAI生成→一括送信"""
    targets = (
        db.query(Lead)
        .filter(
            Lead.status.in_([LeadStatus.NEW, LeadStatus.RESEARCHED]),
            Lead.status != LeadStatus.EXCLUDED,
            Lead.lead_score >= 20,
            Lead.contact_email.isnot(None),
        )
        .order_by(desc(Lead.lead_score))
        .limit(limit)
        .all()
    )

    if not targets:
        return JSONResponse({"message": "⚠️ 送信対象がありません（スコア40以上・メールアドレス必須）"})

    lead_ids = [l.id for l in targets]

    def _run():
        from database.db import SessionLocal
        from outreach.template_generator import generate_email
        from gmail_integration.sender import send_outreach_via_gmail
        from database.models import Lead, ServiceType
        session = SessionLocal()
        sent = 0
        errors = 0
        try:
            for lead_id in lead_ids:
                lead = session.query(Lead).filter(Lead.id == lead_id).first()
                if not lead:
                    continue
                try:
                    email_data = generate_email(lead)
                    stype = ServiceType(email_data.get("service_type", "mixed"))
                    send_outreach_via_gmail(
                        session, lead,
                        email_data["subject"],
                        email_data["body"],
                        email_data.get("body_html", email_data["body"]),
                        stype,
                    )
                    session.commit()
                    sent += 1
                    import time; time.sleep(1.5)
                except Exception as e:
                    logger.error(f"batch send error [{lead.company_name}]: {e}")
                    session.rollback()
                    errors += 1
        finally:
            session.close()
        logger.info(f"一括送信完了: 成功{sent}件 / エラー{errors}件")

    background_tasks.add_task(_run)
    return JSONResponse({"message": f"✅ {len(lead_ids)}件の一括送信を開始しました（バックグラウンド実行中）"})


@app.post("/outreach/save-draft")
async def save_draft(
    lead_id: int = Form(...),
    subject: str = Form(...),
    body: str = Form(...),
    body_html: str = Form(...),
    service_type: str = Form(...),
    db: Session = Depends(get_db),
):
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(404)
    stype = ServiceType(service_type)
    create_outreach_log(db, lead, subject, body, body_html, stype)
    db.commit()
    return JSONResponse({"success": True})


# ─── ⑤ 反響検知 ────────────────────────────────────────────

@app.get("/track/open/{token}")
async def track_open(token: str, request: Request, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else ""
    ua = request.headers.get("user-agent", "")
    record_open(db, token, ip, ua)
    gif = b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04\x00\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
    return Response(content=gif, media_type="image/gif")


@app.get("/track/click/{token}")
async def track_click(
    token: str, request: Request, url: str = Query(...), db: Session = Depends(get_db)
):
    ip = request.client.host if request.client else ""
    ua = request.headers.get("user-agent", "")
    redirect_url = record_click(db, token, url, ip, ua)
    return RedirectResponse(redirect_url)


@app.get("/form/{token}", response_class=HTMLResponse)
async def inquiry_form(token: str, request: Request):
    return templates.TemplateResponse("inquiry_form.html", {"request": request, "token": token})


@app.post("/form/{token}/submit")
async def submit_form(
    token: str, request: Request,
    name: str = Form(...), company: str = Form(...),
    email: str = Form(...), phone: str = Form(""), message: str = Form(""),
    db: Session = Depends(get_db),
):
    ip = request.client.host if request.client else ""
    record_form_submit(db, token, {
        "name": name, "company": company,
        "email": email, "phone": phone, "message": message,
    }, ip)
    return templates.TemplateResponse("form_thanks.html", {"request": request})


@app.get("/unsubscribe/{token}")
async def unsubscribe(token: str, db: Session = Depends(get_db)):
    record_unsubscribe(db, token)
    return HTMLResponse("<html><body><p>配信停止しました。ご迷惑をおかけして申し訳ございません。</p></body></html>")


# ─── ⑥ テレアポリスト（Sheetsエクスポート）───────────────────

@app.get("/hot-leads", response_class=HTMLResponse)
async def hot_leads_page(request: Request, db: Session = Depends(get_db)):
    hot = (
        db.query(Lead)
        .filter(Lead.status.in_([
            LeadStatus.RESPONDED, LeadStatus.REPLIED, LeadStatus.CLICKED,
            LeadStatus.OPENED, LeadStatus.CALLING, LeadStatus.CONNECTED,
        ]))
        .order_by(desc(Lead.lead_score)).all()
    )
    from sheets_integration.client import is_sheets_configured
    sheets_ok = is_sheets_configured()
    return templates.TemplateResponse("hot_leads.html", {
        "request": request, "hot_leads": hot,
        "sheets_ok": sheets_ok,
        "statuses": [s.value for s in LeadStatus],
    })


@app.post("/hot-leads/export-sheets")
async def export_to_sheets(db: Session = Depends(get_db)):
    from sheets_integration.exporter import export_hot_leads_to_sheets
    try:
        result = export_hot_leads_to_sheets(db)
        return JSONResponse(result)
    except Exception as e:
        raise HTTPException(500, str(e))


# ─── ⑦ テレアポ架電管理 ──────────────────────────────────────

@app.get("/calls", response_class=HTMLResponse)
async def calls_page(request: Request, db: Session = Depends(get_db)):
    """コール管理画面: 今日架電すべきリストを優先度順に表示"""
    from datetime import date, timedelta

    # 再架電予定が今日以前のもの + ホットリード
    today = datetime.utcnow().replace(hour=23, minute=59)

    call_targets = (
        db.query(Lead)
        .filter(
            Lead.status.in_([
                LeadStatus.RESPONDED, LeadStatus.REPLIED, LeadStatus.CLICKED,
                LeadStatus.CALLING, LeadStatus.CONNECTED, LeadStatus.EMAIL_SENT,
                LeadStatus.FOLLOWUP_SENT,
            ])
        )
        .order_by(desc(Lead.lead_score))
        .all()
    )

    # 再架電予定日でソート
    def sort_key(lead):
        # 最新コールログの再架電日
        if lead.call_logs:
            last_call = sorted(lead.call_logs, key=lambda c: c.called_at or datetime.min, reverse=True)[0]
            if last_call.next_action_date:
                return (0, -lead.lead_score)
        # HOT（反響あり）を最優先
        if lead.status in (LeadStatus.RESPONDED, LeadStatus.REPLIED):
            return (-1, -lead.lead_score)
        return (1, -lead.lead_score)

    call_targets.sort(key=sort_key)

    today_logs = (
        db.query(CallLog)
        .filter(CallLog.called_at >= datetime.utcnow().replace(hour=0, minute=0))
        .order_by(desc(CallLog.called_at))
        .all()
    )

    return templates.TemplateResponse("calls.html", {
        "request": request,
        "call_targets": call_targets,
        "today_logs": today_logs,
        "statuses": [s.value for s in LeadStatus],
    })


@app.post("/calls/log")
async def log_call(
    lead_id: int = Form(...),
    caller_name: str = Form(...),
    result: str = Form(...),
    next_action: str = Form(""),
    next_action_date: Optional[str] = Form(None),
    notes: str = Form(""),
    duration_seconds: int = Form(0),
    db: Session = Depends(get_db),
):
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(404)

    next_dt = None
    if next_action_date:
        try:
            next_dt = datetime.strptime(next_action_date, "%Y-%m-%dT%H:%M")
        except ValueError:
            pass

    call = CallLog(
        lead_id=lead_id, caller_name=caller_name,
        result=result, next_action=next_action,
        next_action_date=next_dt, notes=notes,
        duration_seconds=duration_seconds,
    )
    db.add(call)

    status_map = {
        "アポ獲得": LeadStatus.MEETING_SET,
        "お断り": LeadStatus.REJECTED,
        "繋がった": LeadStatus.CONNECTED,
        "再架電": LeadStatus.CALLING,
        "折り返し": LeadStatus.CALLING,
    }
    if new_status := status_map.get(result):
        lead.status = new_status

    db.commit()
    return JSONResponse({"success": True})


# ─── ⑧ 結果記録・分析 ────────────────────────────────────────

@app.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request, db: Session = Depends(get_db)):
    stats = _get_stats(db)

    opened = db.query(func.count(OutreachLog.id)).filter(OutreachLog.opened_at.isnot(None)).scalar()
    clicked = db.query(func.count(OutreachLog.id)).filter(OutreachLog.clicked_at.isnot(None)).scalar()
    replied = db.query(func.count(OutreachLog.id)).filter(OutreachLog.replied_at.isnot(None)).scalar()
    followups = db.query(func.count(FollowUpLog.id)).filter(FollowUpLog.result == "sent").scalar()

    sent = stats["sent"]
    reply_rate = round(replied / sent * 100, 1) if sent > 0 else 0
    click_rate = round(clicked / sent * 100, 1) if sent > 0 else 0
    apo_rate = round(stats["meetings"] / stats["calls"] * 100, 1) if stats["calls"] > 0 else 0

    status_dist = (
        db.query(Lead.status, func.count(Lead.id))
        .group_by(Lead.status)
        .all()
    )

    call_results = (
        db.query(CallLog.result, func.count(CallLog.id))
        .group_by(CallLog.result)
        .all()
    )

    from sheets_integration.client import is_sheets_configured
    sheets_ok = is_sheets_configured()

    return templates.TemplateResponse("analytics.html", {
        "request": request,
        "stats": stats,
        "opened": opened, "clicked": clicked, "replied": replied,
        "followups": followups,
        "reply_rate": reply_rate, "click_rate": click_rate, "apo_rate": apo_rate,
        "status_dist": status_dist, "call_results": call_results,
        "sheets_ok": sheets_ok,
    })


@app.post("/analytics/export-sheets")
async def export_analytics(db: Session = Depends(get_db)):
    from sheets_integration.exporter import write_analytics_to_sheets, sync_call_logs_to_sheets
    try:
        r1 = write_analytics_to_sheets(db)
        r2 = sync_call_logs_to_sheets(db)
        return JSONResponse({**r1, **r2})
    except Exception as e:
        raise HTTPException(500, str(e))


# ─── スケジューラ手動実行 ────────────────────────────────────

@app.post("/scheduler/run/{job_id}")
async def run_scheduler_job(job_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    def _run():
        from database.db import SessionLocal
        session = SessionLocal()
        try:
            if job_id == "follow_up":
                from scheduler.follow_up import check_and_send_followups
                check_and_send_followups(session)
            elif job_id == "gmail_reply_check":
                from scheduler.follow_up import run_gmail_reply_check
                run_gmail_reply_check(session)
            elif job_id == "sheets_export":
                from scheduler.follow_up import run_sheets_export
                run_sheets_export(session)
            session.commit()
        finally:
            session.close()

    background_tasks.add_task(_run)
    return JSONResponse({"message": f"{job_id} を手動実行しました"})


# ─── リード自動収集（Googleマップ） ──────────────────────────

@app.get("/discover", response_class=HTMLResponse)
async def discover_page(request: Request):
    return templates.TemplateResponse("discover.html", {"request": request})


@app.post("/discover/run")
async def discover_run(
    background_tasks: BackgroundTasks,
    keyword: str = Form(...),
    area: str = Form(...),
    industry: str = Form(""),
    max_results: int = Form(20),
    db: Session = Depends(get_db),
):
    from scraper.maps_scraper import scrape_google_maps, import_maps_results
    try:
        results = await scrape_google_maps(
            keyword=keyword,
            area=area,
            industry=industry or keyword,
            max_results=max_results,
        )
        if not results:
            return JSONResponse({"error": "企業が見つかりませんでした。キーワードやエリアを変えてお試しください。", "created": 0, "skipped": 0})
        stats = import_maps_results(db, results, source_keyword=f"{area} {keyword}")
        return JSONResponse(stats)
    except Exception as e:
        logger.error(f"discover error: {e}")
        return JSONResponse({"error": str(e), "created": 0, "skipped": 0})


# ─── API ────────────────────────────────────────────────────

@app.get("/api/stats")
async def api_stats(db: Session = Depends(get_db)):
    return _get_stats(db)


@app.get("/api/scheduler/status")
async def api_scheduler_status():
    from scheduler.runner import get_scheduler_status
    return get_scheduler_status()
