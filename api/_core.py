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
import re
import secrets
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

def edit(message_id: int, text: str, buttons: list | None = None) -> None:
    tg("editMessageText", chat_id=CHAT_ID, message_id=message_id, text=text, parse_mode="HTML",
       reply_markup={"inline_keyboard": [buttons] if buttons else []})

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

def transcribe(audio: bytes) -> str:
    last_error = None
    for model in ["gpt-4o-transcribe", "whisper-1"]:
        resp = requests.post("https://api.openai.com/v1/audio/transcriptions", timeout=120,
                             headers={"Authorization": f"Bearer {OPENAI_KEY}"},
                             files={"file": ("voice.ogg", audio, "audio/ogg")},
                             data={"model": model, "language": "he"})
        if resp.ok:
            return resp.json().get("text", "").strip()
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

Extract ALL actionable items from the message.

Rules:
- "cancel" = the user asks to cancel / delete / remove something that is ALREADY in his calendar
  (e.g., "בטל לי את הנדסת חשמל מחר", "תמחק את הפגישה עם דני", "אין לי היום מבנה מחשבים, תוריד").
  query = only the event's name words, without "בטל/את/לי/מחר" (e.g., "הנדסת חשמל").
  date = the day he named, or null if he named no day. NEVER turn a cancel request into a task.
  A to-do about cancelling something outside the calendar ("לבטל את המנוי לנטפליקס") is a task, not cancel.
- "task" = to-do without a specific hour (e.g., "לקנות חלב") -> date = today unless specified. start_time = null, end_time = null.
- "event" = has a specific hour (e.g., "אימון ב-6 בבוקר") -> extract date and time. Assume 1 hour duration if no end time.
- "ignore" = greeting, non-actionable text.
Resolve relative days ("מחר", "ביום שלישי") against today.

Return ONLY a JSON object: {{"items": [{{"type": "task|event|cancel|ignore", "title": "...", "query": "... (cancel only)",
"date": "DD/MM/YYYY or null", "start_time": "HH:MM or null", "end_time": "HH:MM or null"}}]}}

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

DAYS_HE = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]

def day_label(date_str: str) -> str:
    day = datetime.strptime(_parse_date(date_str), "%Y-%m-%d")
    return f"{DAYS_HE[day.weekday()]} {day:%d/%m}"

# ── 🗑️ מחיקה מהיומן: "בטל לי את הנדסת חשמל מחר" ─────────────────────────────

def matches(query: str, summary: str) -> bool:
    words = [w for w in re.findall(r"\w+", (query or "").lower()) if len(w) > 1]
    return bool(words) and all(w in (summary or "").lower() for w in words)

def event_line(ev: dict) -> str:
    when = "כל היום" if ev["end"] is None else f"{ev['start']:%H:%M}–{ev['end']:%H:%M}"
    return f"🗑️ {DAYS_HE[ev['start'].weekday()]} {ev['start']:%d/%m} · {when} · {ev['summary']}"

def delete_buttons(found: list) -> list:
    # shortcut: מזהה אירוע ארוך מ-60 תווים לא נכנס לכפתור טלגרם (64 בתים) — האירוע מוצג בלי כפתור
    return [{"text": "🗑️ מחק מהיומן" if len(found) == 1 else f"🗑️ {ev['start']:%d/%m %H:%M}",
             "callback_data": f"del:{ev['id']}"} for ev in found if len(f"del:{ev['id']}".encode()) <= 64]

def _event(it: dict) -> dict:
    s, e = it.get("start", {}), it.get("end", {})
    if "dateTime" in s:
        return {"id": it["id"], "summary": it.get("summary", "(ללא שם)"),
                "start": datetime.fromisoformat(s["dateTime"]).astimezone(IL_TZ),
                "end": datetime.fromisoformat(e["dateTime"]).astimezone(IL_TZ)}
    return {"id": it["id"], "summary": it.get("summary", "(ללא שם)"),
            "start": datetime.fromisoformat(s["date"]).replace(tzinfo=IL_TZ), "end": None}

def find_events(query: str, date_str) -> list:
    """היום שנבחר, או השבועיים הקרובים אם לא נאמר יום. רק היומן הראשי."""
    if date_str:
        start = datetime.strptime(_parse_date(date_str), "%Y-%m-%d").replace(tzinfo=IL_TZ)
        end = start + timedelta(days=1)
    else:
        start = datetime.now(IL_TZ)
        end = start + timedelta(days=14)
    items = calendar().events().list(calendarId=CALENDAR_ID, timeMin=start.isoformat(), timeMax=end.isoformat(),
                                     singleEvents=True, orderBy="startTime", maxResults=250).execute().get("items", [])
    return [_event(it) for it in items if matches(query, it.get("summary", ""))][:5]

def ask_cancel(item: dict) -> None:
    query, date_str = item.get("query") or item.get("title", ""), item.get("date")
    found = find_events(query, date_str)
    if not found:
        where = f"ב{day_label(date_str)}" if date_str else "בשבועיים הקרובים"
        send(f"🤷 לא מצאתי ביומן \"{esc(query)}\" {where}. לא מחקתי ולא הוספתי כלום.")
        return
    send("🗑️ <b>למחוק מהיומן?</b>\n" + esc("\n".join(event_line(ev) for ev in found)),
         delete_buttons(found) + [{"text": "❌ בטל", "callback_data": "cancel"}])

def delete_event(message: dict, event_id: str) -> None:
    service = calendar()
    try:
        line = event_line(_event(service.events().get(calendarId=CALENDAR_ID, eventId=event_id).execute()))
        service.events().delete(calendarId=CALENDAR_ID, eventId=event_id).execute()
        edit(message["message_id"], f"🗑️ <b>נמחק מהיומן:</b>\n{esc(line)}")
    except Exception as ex:
        if "404" in str(ex) or "410" in str(ex):
            edit(message["message_id"], "🤷 האירוע כבר לא ביומן.")
        else:
            raise

# ── ביטול: כל פעולה מקבלת מזהה קצר שנשמר על האירועים/משימות ונכנס לכפתור ↩️ ─────

def new_batch() -> str:
    return secrets.token_hex(4)

def marker(batch: str) -> str:
    return f"yossi:{batch}"

def is_marked(task: dict, batch: str) -> bool:
    return marker(batch) in (task.get("notes") or "")

def undo_button(batch: str) -> list:
    return [{"text": "↩️ בטל את מה שנוסף", "callback_data": f"undo:{batch}"}]

def undo(batch: str) -> int:
    service, removed = calendar(), 0
    for ev in service.events().list(calendarId=CALENDAR_ID, privateExtendedProperty=f"yossi_batch={batch}",
                                    singleEvents=True).execute().get("items", []):
        service.events().delete(calendarId=CALENDAR_ID, eventId=ev["id"]).execute()
        removed += 1
    t = tasks()
    for task in t.tasks().list(tasklist="@default", showCompleted=True, showHidden=True,
                               maxResults=100).execute().get("items", []):
        if is_marked(task, batch):
            t.tasks().delete(tasklist="@default", task=task["id"]).execute()
            removed += 1
    return removed

# ── הוספה ─────────────────────────────────────────────────────────────────────

def add_calendar_event(parsed: dict, batch: str) -> str:
    date_iso = _parse_date(parsed.get("date", ""))
    start_time = parsed.get("start_time") or "09:00"
    start_dt = datetime.strptime(f"{date_iso} {start_time}", "%Y-%m-%d %H:%M").replace(tzinfo=IL_TZ)
    end_time = parsed.get("end_time")
    end_dt = (datetime.strptime(f"{date_iso} {end_time}", "%Y-%m-%d %H:%M").replace(tzinfo=IL_TZ)
              if end_time else start_dt + timedelta(hours=1))
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)  # "22:00–01:00"
    calendar().events().insert(calendarId=CALENDAR_ID, body={
        "summary": f"📅 {parsed.get('title', 'אירוע ללא שם')}",
        "start": {"dateTime": start_dt.isoformat(), "timeZone": "Asia/Jerusalem"},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": "Asia/Jerusalem"},
        "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 30}]},
        "extendedProperties": {"private": {"yossi_batch": batch}},
    }).execute()
    return f"{DAYS_HE[start_dt.weekday()]} {start_dt:%d/%m} {start_time}–{end_dt:%H:%M}"

def add_task(parsed: dict, batch: str) -> None:
    date_iso = _parse_date(parsed.get("date", ""))
    tasks().tasks().insert(tasklist="@default", body={
        "title": parsed.get("title", "משימה ללא שם"),
        "due": f"{date_iso}T00:00:00.000Z",
        "notes": f"נוסף ע\"י יוסי · {marker(batch)}",
    }).execute()

def add_items(items: list) -> tuple:
    """מוסיף משימות ואירועים; מחזיר שורות סיכום ואת מזהה הביטול (None אם לא נוסף כלום)."""
    batch, added, failed = new_batch(), [], []
    for parsed in items:
        kind = parsed.get("type", "task")
        if kind == "ignore":
            continue
        title = parsed.get("title") or "משימה ללא שם"
        if kind == "event" and not parsed.get("start_time"):
            kind = "task"  # אירוע בלי שעה — נכנס כמשימה
        try:
            if kind == "task":
                add_task(parsed, batch)
                added.append(f"✅ {esc(title)} — {day_label(parsed.get('date', ''))}")
            else:
                added.append(f"📅 {esc(title)} — {add_calendar_event(parsed, batch)}")
        except Exception as ex:
            logger.exception("Failed %s", title)
            failed.append(f"• {esc(title)} — {esc(type(ex).__name__)}")
    lines = ["➕ <b>נוסף:</b>", *added] if added else []
    if failed:
        lines += ["", "⚠️ <b>נכשל:</b>", *failed]
    return lines, batch if added else None

def preview_items(items: list, header: str) -> bool:
    """מציג בדיוק מה ייכנס (יום, תאריך, שעה) — נכנס רק אחרי ✅. הטקסט עצמו הוא המקור להוספה."""
    rows = [r for r in map(item_line, items) if r]
    if rows:
        send(f"{header}\n" + esc("\n".join(rows)),
             [{"text": "✅ הוסף", "callback_data": "items"}, {"text": "❌ בטל", "callback_data": "cancel"}])
    return bool(rows)

def handle_text(text: str) -> None:
    now = datetime.now(IL_TZ)
    items = ask_ai(PARSE_PROMPT.format(today=f"{now:%d/%m/%Y}", time=f"{now:%H:%M}", message=text)).get("items", [])
    cancels = [i for i in items if i.get("type") == "cancel"]
    for item in cancels:
        ask_cancel(item)
    adds = [i for i in items if i.get("type") in ("task", "event")]
    if not preview_items(adds, "📝 <b>זה מה שאכניס — לאשר?</b>") and not cancels:
        send("🤷 לא מצאתי בהודעה משימה, אירוע או משהו לבטל.")

# ── תצוגה מקדימה לאישור (צילומי מסך) — הטקסט שלה הוא גם המקור להוספה ─────────

def item_line(item: dict) -> str:
    try:
        day = datetime.strptime(item.get("date") or "", "%d/%m/%Y")
    except ValueError:
        return ""
    head = f"{DAYS_HE[day.weekday()]} {day:%d/%m/%Y}"
    title = " ".join(str(item.get("title") or "").split())  # בלי שורות חדשות — שורה אחת לפריט
    if item.get("type") == "event" and item.get("start_time"):
        start = datetime.strptime(item["start_time"], "%H:%M")
        end = item.get("end_time") or f"{start + timedelta(hours=1):%H:%M}"
        return f"📅 {head} · {item['start_time']}–{end} · {title}"
    return f"📌 {head} · {title}"

ITEM_RE = re.compile(r"(📅|📌) \S+ (\d{2}/\d{2}/\d{4}) · (?:(\d{2}:\d{2})–(\d{2}:\d{2}) · )?(.+)$")

def parse_items(text: str) -> list:
    out = []
    for row in text.splitlines():
        m = ITEM_RE.match(row.strip())
        if m:
            icon, day, start, end, title = m.groups()
            out.append({"type": "event" if icon == "📅" else "task", "title": title.strip(), "date": day,
                        "start_time": start, "end_time": end})
    return out
