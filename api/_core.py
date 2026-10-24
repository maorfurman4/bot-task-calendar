"""
יוסי — משימות ואירועים ליומן מתוך טלגרם.
- משימה (בלי שעה) → Google Tasks
- אירוע (עם שעה) → Google Calendar
רץ כ-webhook ב-Vercel: כל הודעה מטופלת מיד.
"""

import base64
import hashlib
import html
import json
import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials as OAuthCredentials
from google.oauth2.service_account import Credentials as SACredentials
from googleapiclient.discovery import build

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logger = logging.getLogger("yossi")

IL_TZ = ZoneInfo("Asia/Jerusalem")
BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "").strip()
CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID", "maorfurman123@gmail.com")
OPENAI_KEY = os.environ.get("OPEN_API_KEY", "").strip()
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
MODELS = ["gpt-5", "gpt-4o"]  # הראשון שעובד עם המפתח

def esc(text) -> str:
    return html.escape(str(text))

def webhook_secret() -> str:
    # נגזר מהטוקן, כדי שלא יהיה עוד סוד לנהל; אותו חישוב ב-deploy.yml
    return hashlib.sha256(BOT_TOKEN.encode()).hexdigest()[:32]

# ── Telegram ──────────────────────────────────────────────────────────────────

def tg(method: str, **payload) -> dict:
    resp = requests.post(f"{TELEGRAM_API}/{method}", json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json().get("result", {})

def send(text: str, buttons: list | None = None) -> dict:
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": [buttons]}
    return tg("sendMessage", **payload)

def edit(message_id: int, text: str) -> None:
    tg("editMessageText", chat_id=CHAT_ID, message_id=message_id, text=text, parse_mode="HTML")

def download(file_id: str) -> bytes:
    path = tg("getFile", file_id=file_id)["file_path"]
    resp = requests.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}", timeout=30)
    resp.raise_for_status()
    return resp.content

# ── AI ────────────────────────────────────────────────────────────────────────

def ask_ai(prompt: str, image: bytes | None = None) -> dict:
    content = [{"type": "text", "text": prompt}]
    if image:
        url = "data:image/jpeg;base64," + base64.b64encode(image).decode()
        content.append({"type": "image_url", "image_url": {"url": url, "detail": "high"}})
    last_error = None
    for model in MODELS:
        body = {"model": model, "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": content}]}
        body.update({"reasoning_effort": "low"} if model.startswith("gpt-5") else {"temperature": 0.1})
        resp = requests.post("https://api.openai.com/v1/chat/completions", json=body, timeout=240,
                             headers={"Authorization": f"Bearer {OPENAI_KEY}"})
        if resp.ok:
            return json.loads(resp.json()["choices"][0]["message"]["content"])
        last_error = f"{model}: {resp.status_code} {resp.text[:150]}"
        logger.warning("%s — trying next model", last_error)
    raise RuntimeError(last_error)

# ── Google ────────────────────────────────────────────────────────────────────

def calendar():
    info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    creds = SACredentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/calendar"])
    return build("calendar", "v3", credentials=creds, cache_discovery=False)

def tasks():
    info = json.loads(os.environ["GOOGLE_TASKS_CREDENTIALS"])
    creds = OAuthCredentials(
        token=None,
        refresh_token=info["refresh_token"],
        client_id=info["client_id"],
        client_secret=info["client_secret"],
        token_uri=info.get("token_uri", "https://oauth2.googleapis.com/token"),
        scopes=["https://www.googleapis.com/auth/tasks"],
    )
    creds.refresh(Request())
    return build("tasks", "v1", credentials=creds, cache_discovery=False)

# ── טקסט חופשי → משימות ואירועים ──────────────────────────────────────────────

PARSE_PROMPT = """You are a Hebrew personal assistant. The user sent you a message in Hebrew.
Today is {today} (DD/MM/YYYY). Current time (Israel): {time}.

Extract ALL actionable items (tasks and events) from the message.

Rules:
- "task" = to-do without a specific hour (e.g., "לקנות חלב") -> date = today unless specified. start_time = null, end_time = null.
- "event" = has a specific hour (e.g., "אימון ב-6 בבוקר") -> extract date and time. Assume 1 hour duration if no end time.
- "ignore" = greeting, non-actionable text.

Return ONLY a JSON object: {{"items": [{{"type": "task|event|ignore", "title": "...", "date": "DD/MM/YYYY",
"start_time": "HH:MM or null", "end_time": "HH:MM or null"}}]}}

Message:
{message}
"""

def _parse_date(date_str: str) -> str:
    if not date_str or "/" not in date_str:
        date_str = datetime.now(IL_TZ).strftime("%d/%m/%Y")
    parts = date_str.split("/")
    day, month = parts[0], parts[1]
    year = parts[2] if len(parts) == 3 else str(datetime.now(IL_TZ).year)
    return f"{'20' + year if len(year) == 2 else year}-{month.zfill(2)}-{day.zfill(2)}"

def add_calendar_event(parsed: dict) -> str:
    date_iso = _parse_date(parsed.get("date", ""))
    start_time = parsed.get("start_time") or "09:00"
    start_dt = datetime.strptime(f"{date_iso} {start_time}", "%Y-%m-%d %H:%M").replace(tzinfo=IL_TZ)
    end_time = parsed.get("end_time")
    end_dt = (datetime.strptime(f"{date_iso} {end_time}", "%Y-%m-%d %H:%M").replace(tzinfo=IL_TZ)
              if end_time else start_dt + timedelta(hours=1))
    calendar().events().insert(calendarId=CALENDAR_ID, body={
        "summary": f"📅 {parsed.get('title', 'אירוע ללא שם')}",
        "start": {"dateTime": start_dt.isoformat(), "timeZone": "Asia/Jerusalem"},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": "Asia/Jerusalem"},
        "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 30}]},
    }).execute()
    return f"{start_dt:%d/%m} {start_time}–{end_dt:%H:%M}"

def add_task(parsed: dict) -> None:
    date_iso = _parse_date(parsed.get("date", ""))
    tasks().tasks().insert(tasklist="@default", body={
        "title": parsed.get("title", "משימה ללא שם"),
        "due": f"{date_iso}T00:00:00.000Z",
    }).execute()

def handle_text(text: str) -> None:
    now = datetime.now(IL_TZ)
    items = ask_ai(PARSE_PROMPT.format(today=f"{now:%d/%m/%Y}", time=f"{now:%H:%M}", message=text)).get("items", [])
    added, failed = [], []
    for parsed in items:
        kind = parsed.get("type", "task")
        if kind == "ignore":
            continue
        title = parsed.get("title") or "משימה ללא שם"
        if kind == "event" and not parsed.get("start_time"):
            kind = "task"  # אירוע בלי שעה — נכנס כמשימה
        try:
            if kind == "task":
                add_task(parsed)
                added.append(f"✅ {esc(title)}")
            else:
                added.append(f"📅 {esc(title)} — {add_calendar_event(parsed)}")
        except Exception as ex:
            logger.exception("Failed %s", title)
            failed.append(f"• {esc(title)} — {esc(type(ex).__name__)}")
    if not added and not failed:
        send("🤷 לא מצאתי בהודעה משימה או אירוע להוסיף.")
        return
    lines = ["➕ <b>נוסף:</b>", *added] if added else []
    if failed:
        lines += ["", "⚠️ <b>נכשל:</b>", *failed]
    send("\n".join(lines))
