"""
סידור עבודה מתמונה → משמרות של מאור ביומן, אחרי אישור.
ה-AI רק מאתר את התאים עם השם; את השעות מחשב הקוד לפי הכללים.
"""

import json
import re
from datetime import date, datetime, timedelta

from _core import (CALENDAR_ID, DAYS_HE, IL_TZ, add_items, ask_ai, calendar, edit, esc, logger,
                   parse_items, preview_items, send, span, undo_button, unspan)

TAG = {"yossi": "shift"}  # מסמן משמרות שיוסי הכניס, כדי להחליף אותן כששולחים סידור מתוקן

REGULAR = {"בוקר": ("07:00", "15:00"), "צהריים": ("15:00", "23:00"), "לילה": ("23:00", "07:00")}
TWELVE = {"בוקר": ("07:00", "19:00"), "לילה": ("19:00", "07:00")}  # יום עם 12/12 באדום

VISION_PROMPT = """This image was sent by Maor (Israel). Today is {today} (DD/MM/YYYY, {weekday}).

Decide what it is:
A) "schedule" — a weekly work schedule (a table of days × shifts × positions, with employee names in the cells).
B) "events" — anything else with something to put in a calendar or to-do list: a WhatsApp/SMS message about an
   appointment, an invitation, a ticket, a booking confirmation, a reminder, a list of things to do.
C) "other" — nothing actionable.

For A: find EVERY cell that contains the name "מאור פורמן" (also written "פורמן מאור", or partly cut off).
For each such cell return:
- date: the date in that cell's day header, as DD/MM/YYYY
- shift: that cell's sub-column header — exactly one of "בוקר", "צהריים", "לילה"
- position: that cell's row label on the right side, copied exactly (e.g. "מאבטח 1 אקדח", "שיפוצים 7:00-17:00")
- twelve: true only if, in the SAME row and SAME day, the "צהריים" cell shows a red "12/12"

Be very careful to map each cell to the correct day and sub-column: the table is right-to-left, and each day has
three sub-columns (בוקר on the right, צהריים in the middle, לילה on the left). Never guess; if the name is not in the
table, return an empty list.

For B: return each item. "event" has a specific time; "task" has none. Resolve relative dates ("מחר", "ביום שלישי")
against today. title in Hebrew, short and clear, including the place if one is given (e.g. "רופא שיניים — מרפאת הדר").
Never invent a date or time that is not in the image; if there is no date, use today.

Return ONLY JSON:
{{"kind": "schedule|events|other",
  "shifts": [{{"date": "...", "shift": "...", "position": "...", "twelve": false}}],
  "items": [{{"type": "event|task", "title": "...", "date": "DD/MM/YYYY", "start_time": "HH:MM or null", "end_time": "HH:MM or null"}}]}}"""

# ── שעות ──────────────────────────────────────────────────────────────────────

def _add_hours(hhmm: str, hours: int) -> str:
    h, m = map(int, hhmm.split(":"))
    return f"{(h + hours) % 24:02d}:{m:02d}"

def shift_hours(position: str, shift: str, twelve: bool) -> tuple:
    hours = re.search(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", position)
    if hours:  # שעות כתובות בשורה, למשל "שיפוצים 7:00-17:00"
        h1, m1, h2, m2 = hours.groups()
        return f"{int(h1):02d}:{m1}", f"{int(h2):02d}:{m2}"
    start, end = (TWELVE if twelve and shift in TWELVE else REGULAR)[shift]
    if "רמש" in re.sub(r"[\"״׳']", "", position):  # רמ"ש מתחיל שעה מוקדם יותר
        start, end = _add_hours(start, -1), _add_hours(end, -1)
    return start, end

def make_event(day: date, shift: str, start: str, end: str, position: str) -> dict:
    s = datetime.combine(day, datetime.strptime(start, "%H:%M").time(), IL_TZ)
    e = datetime.combine(day, datetime.strptime(end, "%H:%M").time(), IL_TZ)
    if e <= s:
        e += timedelta(days=1)  # משמרת לילה נגמרת למחרת
    return {"start": s, "end": e, "shift": shift, "position": position}

def week_start(day: date) -> date:
    return day - timedelta(days=(day.weekday() + 1) % 7)  # יום ראשון

# ── תצוגה מקדימה — הטקסט שלה הוא גם המקור לאישור (בלי לשמור מצב בשרת) ─────────

def line(ev: dict) -> str:
    hours = span(ev["start"].strftime("%H:%M"), ev["end"].strftime("%H:%M"))
    return f"🛡️ {DAYS_HE[ev['start'].weekday()]} {ev['start']:%d/%m} · {ev['shift']} · {hours} · {ev['position']}"

LINE_RE = re.compile(r"(\d{2})/(\d{2}) · (.+?) · (\d{2}:\d{2})–(\d{2}:\d{2}) · (.+)$")

def parse_preview(text: str, ws: date) -> list:
    events = []
    for row in unspan(text).splitlines():
        m = LINE_RE.search(row)
        if not row.startswith("🛡") or not m:
            continue
        d, mo, shift, start, end, position = m.groups()
        year = ws.year + (1 if int(mo) < ws.month else 0)  # שבוע שחוצה את סוף דצמבר
        events.append(make_event(date(year, int(mo), int(d)), shift, start, end, position.strip()))
    return events

def _key(ev: dict) -> tuple:
    return ev["start"].isoformat(), ev["end"].isoformat(), ev["position"]

def diff(old: list, new: list) -> list:
    if not old:
        return []
    old_keys, new_keys = {_key(e): e for e in old}, {_key(e): e for e in new}
    if old_keys.keys() == new_keys.keys():
        return ["✔️ זהה לסידור שכבר ביומן"]
    return [f"➕ נוסף: {esc(line(e)[3:])}" for k, e in new_keys.items() if k not in old_keys] + \
           [f"➖ בוטל: {esc(line(e)[3:])}" for k, e in old_keys.items() if k not in new_keys]

def preview_text(events: list, ws: date, changes: list, conflicts: list) -> str:
    out = [f"📋 <b>מצאתי {len(events)} משמרות שלך ({ws:%d/%m}–{ws + timedelta(days=6):%d/%m}):</b>",
           *[esc(line(e)) for e in events]]
    if changes:
        out += ["", "🔄 <b>מה השתנה מהסידור שביומן:</b>", *changes]
    if conflicts:
        out += ["", "⚠️ <b>התנגשויות ביומן:</b>", *conflicts]
    return "\n".join(out)

# ── Google Calendar ───────────────────────────────────────────────────────────

def _bounds(ws: date) -> tuple:
    start = datetime.combine(ws, datetime.min.time(), IL_TZ)
    return start.isoformat(), (start + timedelta(days=7)).isoformat()

def existing_shifts(ws: date) -> list:
    t_min, t_max = _bounds(ws)
    items = calendar().events().list(
        calendarId=CALENDAR_ID, timeMin=t_min, timeMax=t_max, singleEvents=True,
        privateExtendedProperty="yossi=shift",
    ).execute().get("items", [])
    out = []
    for it in items:
        ev = {
            "id": it["id"],
            "start": datetime.fromisoformat(it["start"]["dateTime"]).astimezone(IL_TZ),
            "end": datetime.fromisoformat(it["end"]["dateTime"]).astimezone(IL_TZ),
            "shift": it.get("extendedProperties", {}).get("private", {}).get("shift", ""),
            "position": it.get("extendedProperties", {}).get("private", {}).get("position", ""),
        }
        if ev["start"] >= datetime.fromisoformat(t_min):  # רק משמרות שמתחילות בשבוע הזה
            out.append(ev)
    return out

def conflicts(events: list) -> list:
    out = []
    for ev in events:
        items = calendar().events().list(
            calendarId=CALENDAR_ID, timeMin=ev["start"].isoformat(), timeMax=ev["end"].isoformat(), singleEvents=True,
        ).execute().get("items", [])
        for it in items:
            if "dateTime" not in it.get("start", {}) or it.get("extendedProperties", {}).get("private", {}).get("yossi"):
                continue  # אירוע של כל היום או משמרת קודמת שתוחלף
            s = datetime.fromisoformat(it["start"]["dateTime"]).astimezone(IL_TZ)
            e = datetime.fromisoformat(it["end"]["dateTime"]).astimezone(IL_TZ)
            mine = span(ev["start"].strftime("%H:%M"), ev["end"].strftime("%H:%M"))
            out.append(f"• {DAYS_HE[ev['start'].weekday()]} {ev['start']:%d/%m} ({mine}) "
                       f"מתנגש עם: {esc(it.get('summary', '(ללא שם)'))} {span(f'{s:%H:%M}', f'{e:%H:%M}')}")
    return out

def replace_week(ws: date, events: list) -> None:
    service = calendar()
    for old in existing_shifts(ws):
        service.events().delete(calendarId=CALENDAR_ID, eventId=old["id"]).execute()
    for ev in events:
        service.events().insert(calendarId=CALENDAR_ID, body={
            "summary": f"🛡️ משמרת {ev['shift']} — {ev['position']}",
            "start": {"dateTime": ev["start"].isoformat(), "timeZone": "Asia/Jerusalem"},
            "end": {"dateTime": ev["end"].isoformat(), "timeZone": "Asia/Jerusalem"},
            "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 30}]},
            "extendedProperties": {"private": {**TAG, "shift": ev["shift"], "position": ev["position"]}},
        }).execute()

# ── קיצורי טקסט: "ראשון שלישי וחמישי משמרת צהריים" ───────────────────────────

SHIFT_WORDS = {"בוקר": "בוקר", "צהריים": "צהריים", "צהרים": "צהריים", "לילה": "לילה"}
DAY_RE = re.compile(r"^(?:ו?ב?|ב?ו?)(ראשון|שני|שלישי|רביעי|חמישי|שישי|שבת)$")

def parse_shortcut(text: str, today: date) -> list:
    """רק הודעה עם "משמרת"; כל פסקה (מופרדת בפסיק/נקודה/שורה): ימים + סוג משמרת + רמ"ש/12/כפולה אופציונלי."""
    if "משמר" not in text:
        return []
    ws = week_start(today)
    if "שבוע הבא" in text or ("השבוע" not in text and today != ws):
        ws += timedelta(days=7)  # ברירת מחדל: סידור לשבוע הבא, אלא אם היום ראשון
    events, carry = [], None  # carry: "משמרת צהריים:" בשורה בלי ימים — חל על השורות שאחריה
    for clause in re.split(r"[,.\n;]", text):
        flat = re.sub(r"[\"״׳']", "", clause)
        position = 'רמ"ש' if "רמש" in flat else "רגילה"
        twelve = bool(re.search(r"(?<!\d)12(?!\d)", clause)) or "כפול" in clause
        days, shift, pairs = [], None, []
        for word in re.findall(r"[\w\"״׳']+", clause):
            day = DAY_RE.match(word.strip("\"'"))
            if day:
                days.append(DAYS_HE.index(day.group(1)))
            elif (sw := word if word in SHIFT_WORDS else word[1:] if word[:1] == "ב" else "") in SHIFT_WORDS:
                shift = SHIFT_WORDS[sw]  # "בוקר" עצמו מתחיל ב-ב, לכן לא lstrip
                pairs += [(d, shift) for d in days]
                days = []
        pairs += [(d, shift or carry) for d in days if shift or carry]  # "משמרת צהריים בשני" — הסוג לפני הימים
        if shift and not pairs:
            carry = shift
        for weekday, sh in pairs:
            day = ws + timedelta(days=(weekday + 1) % 7)  # weekday 0=שני → ראשון הוא 0 בשבוע
            start, end = shift_hours(position, sh, twelve)
            events.append(make_event(day, sh, start, end, position))
    return sorted(events, key=lambda e: e["start"])

# ── זרימה ─────────────────────────────────────────────────────────────────────

def to_events(found: list) -> list:
    events = []
    for s in found:
        if s.get("shift") not in REGULAR:
            continue
        day = datetime.strptime(s["date"], "%d/%m/%Y").date()
        start, end = shift_hours(s.get("position", ""), s["shift"], bool(s.get("twelve")))
        events.append(make_event(day, s["shift"], start, end, s.get("position", "").strip()))
    return sorted(events, key=lambda e: e["start"])

def handle_image(image: bytes) -> None:
    send("🔍 קורא את התמונה...")
    today = datetime.now(IL_TZ)
    result = ask_ai(VISION_PROMPT.format(today=f"{today:%d/%m/%Y}", weekday=DAYS_HE[today.weekday()]), image)
    logger.info("vision: %d bytes → %s", len(image), json.dumps(result, ensure_ascii=False))
    if result.get("kind") == "events":
        if preview_items(result.get("items", []), "📸 <b>מצאתי בתמונה:</b>"):
            return
    if result.get("kind") != "schedule":
        send("🤷 לא מצאתי בתמונה סידור עבודה, אירוע או משימה.")
        return
    events = to_events(result.get("shifts", []))
    if not events:
        send("🤷 לא הצלחתי לקרוא את השם שלך (מאור פורמן) בתמונה.\n"
             "אפשר לכתוב לי במקום, למשל: <b>משמרת צהריים בראשון שלישי וחמישי</b>")
        return
    ask_approval(events)

def handle_shortcut(text: str) -> bool:
    events = parse_shortcut(text, datetime.now(IL_TZ).date())
    if events:
        ask_approval(events)
    return bool(events)

def ask_approval(events: list) -> None:
    ws = week_start(events[0]["start"].date())
    text = preview_text(events, ws, diff(existing_shifts(ws), events), conflicts(events))
    send(text, [{"text": "✅ אשר והכנס ליומן", "callback_data": f"shifts:{ws.isoformat()}"},
                {"text": "❌ בטל", "callback_data": "cancel"}])

def approve(message: dict, ws_iso: str) -> None:
    ws = date.fromisoformat(ws_iso)
    events = parse_preview(message.get("text", ""), ws)
    if not events:  # בלי זה הלחיצה הייתה מוחקת את משמרות השבוע ולא מכניסה כלום
        send("⚠️ לא הצלחתי לקרוא את המשמרות מההודעה. שלח את התמונה שוב.")
        return
    replace_week(ws, events)
    edit(message["message_id"], "\n".join([*[esc(line(e)) for e in events], "",
                                           f"✅ <b>{len(events)} משמרות נכנסו ליומן</b>"]))

def approve_items(message: dict) -> None:
    """אישור של צילום מסך: הפריטים נקראים מטקסט התצוגה המקדימה."""
    lines, batch = add_items(parse_items(message.get("text", "")))
    edit(message["message_id"], "\n".join(lines) or "⚠️ לא הצלחתי לקרוא את הפריטים מההודעה.",
         undo_button(batch) if batch else None)
