"""בדיקות קצרות: python test_yossi.py"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "api"))
import _shifts as s  # noqa: E402

# שעות לפי הכללים של מאור
assert s.shift_hours("מאבטח 1 אקדח", "בוקר", False) == ("07:00", "15:00")
assert s.shift_hours("הרצליה", "צהריים", False) == ("15:00", "23:00")
assert s.shift_hours('רמ"ש', "לילה", False) == ("22:00", "06:00")
assert s.shift_hours("רמ״ש", "צהריים", False) == ("14:00", "22:00")  # גרשיים עבריים
assert s.shift_hours("צלף", "בוקר", True) == ("07:00", "19:00")
assert s.shift_hours("צלף", "לילה", True) == ("19:00", "07:00")
assert s.shift_hours('רמ"ש', "לילה", True) == ("18:00", "06:00")
assert s.shift_hours("שיפוצים 7:00-17:00", "בוקר", False) == ("07:00", "17:00")

# הסידור לדוגמה (שבוע 25/10/2026): 3 משמרות
found = [
    {"date": "29/10/2026", "shift": "לילה", "position": 'רמ"ש', "twelve": False},
    {"date": "25/10/2026", "shift": "צהריים", "position": "מאבטח 1 אקדח", "twelve": False},
    {"date": "26/10/2026", "shift": "צהריים", "position": "הרצליה", "twelve": False},
    {"date": "26/10/2026", "shift": "משהו", "position": "x", "twelve": False},  # לא חוקי — מסונן
]
events = s.to_events(found)
assert [s.line(e) for e in events] == [
    "🛡️ ראשון 25/10 · צהריים · ⁦15:00–23:00⁩ · מאבטח 1 אקדח",
    "🛡️ שני 26/10 · צהריים · ⁦15:00–23:00⁩ · הרצליה",
    '🛡️ חמישי 29/10 · לילה · ⁦22:00–06:00⁩ · רמ"ש',
]
assert events[2]["end"].date() == date(2026, 10, 30)  # לילה נגמר למחרת
ws = s.week_start(events[0]["start"].date())
assert ws == date(2026, 10, 25) and s.week_start(date(2026, 10, 31)) == ws

# אישור: המשמרות נקראות בחזרה מטקסט ההודעה בדיוק כמו שהוצגו
text = s.preview_text(events, ws, [], []).replace("&quot;", '"')
assert [s._key(e) for e in s.parse_preview(text, ws)] == [s._key(e) for e in events]
assert s.parse_preview("שורה בלי משמרת\n📋 כותרת", ws) == []

# שבוע שחוצה שנה
jan = s.make_event(date(2027, 1, 2), "בוקר", "07:00", "15:00", "צלף")
assert s.parse_preview(s.line(jan), date(2026, 12, 27))[0]["start"].year == 2027

# מה השתנה
assert s.diff([], events) == []
assert s.diff(events, events) == ["✔️ זהה לסידור שכבר ביומן"]
changed = s.diff(events[:2], events[1:])
assert len(changed) == 2 and changed[0].startswith("➕ נוסף: חמישי") and changed[1].startswith("➖ בוטל: ראשון")

# קיצורי טקסט: "ראשון שלישי וחמישי משמרת צהריים"
wed = date(2026, 10, 28)  # יום רביעי → ברירת מחדל: השבוע הבא (1/11)
def short(text, today=wed):
    return [s.line(e) for e in s.parse_shortcut(text, today)]

assert short("יש לי בראשון שלישי וחמישי משמרת צהריים") == [
    "🛡️ ראשון 01/11 · צהריים · ⁦15:00–23:00⁩ · רגילה",
    "🛡️ שלישי 03/11 · צהריים · ⁦15:00–23:00⁩ · רגילה",
    "🛡️ חמישי 05/11 · צהריים · ⁦15:00–23:00⁩ · רגילה",
]
assert short("משמרות: ראשון ושלישי בוקר, חמישי לילה רמש") == [
    "🛡️ ראשון 01/11 · בוקר · ⁦07:00–15:00⁩ · רגילה",
    "🛡️ שלישי 03/11 · בוקר · ⁦07:00–15:00⁩ · רגילה",
    '🛡️ חמישי 05/11 · לילה · ⁦22:00–06:00⁩ · רמ"ש',
]
assert short("משמרת צהריים בשני") == ["🛡️ שני 02/11 · צהריים · ⁦15:00–23:00⁩ · רגילה"]
assert short("השבוע משמרת לילה בשישי") == ["🛡️ שישי 30/10 · לילה · ⁦23:00–07:00⁩ · רגילה"]
assert short("משמרת בוקר 12 בשבת") == ["🛡️ שבת 07/11 · בוקר · ⁦07:00–19:00⁩ · רגילה"]
assert short("משמרת בראשון", date(2026, 11, 1)) == []  # בלי סוג משמרת — לא קיצור
assert short("אימון בבוקר ביום שלישי") == []  # בלי "משמרת" — משימה רגילה
# סוג המשמרת בשורת כותרת חל על השורות שאחריה; "כפולה" = 12 שעות (ההודעה של מאור, 29/10/2026)
assert short("להכניס ליומן\nמשמרת צהריים :\nראשון רמש\nשני מאבטח\nשלישי רמש\nחמישי רמש\nשבת משמרת כפולה בוקר רמש") == [
    '🛡️ ראשון 01/11 · צהריים · ⁦14:00–22:00⁩ · רמ"ש',
    "🛡️ שני 02/11 · צהריים · ⁦15:00–23:00⁩ · רגילה",
    '🛡️ שלישי 03/11 · צהריים · ⁦14:00–22:00⁩ · רמ"ש',
    '🛡️ חמישי 05/11 · צהריים · ⁦14:00–22:00⁩ · רמ"ש',
    '🛡️ שבת 07/11 · בוקר · ⁦06:00–18:00⁩ · רמ"ש',
]

# צילום מסך → אירועים ומשימות: הטקסט של התצוגה המקדימה הוא גם המקור לאישור
import _core as c  # noqa: E402
items = [
    {"type": "event", "title": "רופא שיניים — מרפאת הדר", "date": "02/11/2026", "start_time": "08:00", "end_time": None},
    {"type": "task", "title": "לקנות חלב", "date": "01/11/2026"},
]
preview = "\n".join(c.item_line(i) for i in items)
assert preview.splitlines() == [
    "📅 שני 02/11/2026 · ⁦08:00–09:00⁩ · רופא שיניים — מרפאת הדר",
    "📌 ראשון 01/11/2026 · לקנות חלב",
]
back = c.parse_items("📸 מצאתי בתמונה:\n" + preview + "\nשורה אחרת")
assert back == [
    {"type": "event", "title": "רופא שיניים — מרפאת הדר", "date": "02/11/2026", "start_time": "08:00", "end_time": "09:00"},
    {"type": "task", "title": "לקנות חלב", "date": "01/11/2026", "start_time": None, "end_time": None},
]
assert c.item_line({"type": "event", "title": "x", "date": "bad"}) == ""  # תאריך לא תקין — לא מוצג

# ביטול: כל פעולה מסומנת במזהה קצר שנכנס לכפתור
b = c.new_batch()
assert len(f"undo:{b}".encode()) <= 64 and c.is_marked({"notes": f"נוסף ע\"י יוסי · {c.marker(b)}"}, b)
assert not c.is_marked({"notes": "x"}, b) and not c.is_marked({}, b)

# 🗑️ "בטל לי את הנדסת חשמל מחר" → חיפוש ביומן, לא משימה
assert c.matches("הנדסת חשמל", "הנדסת חשמל ב' (הרצאה) - גבי שפט")
assert c.matches("הרצאה הנדסת חשמל", "📚 הנדסת חשמל ב' (הרצאה)")  # כל המילים, בכל סדר
assert not c.matches("הנדסת חשמל", "מבנה מחשבים (הרצאה)")
assert not c.matches("", "הנדסת חשמל")
from datetime import datetime as _dt  # noqa: E402
timed = {"id": "abc", "summary": "הנדסת חשמל ב' (הרצאה)",
         "start": _dt(2026, 10, 26, 11, 0, tzinfo=c.IL_TZ), "end": _dt(2026, 10, 26, 14, 0, tzinfo=c.IL_TZ)}
allday = {"id": "def", "summary": "יום ספורט", "start": _dt(2026, 10, 27, tzinfo=c.IL_TZ), "end": None}
assert c.event_line(timed) == "🗑️ שני 26/10 · ⁦11:00–14:00⁩ · הנדסת חשמל ב' (הרצאה)"
assert c.event_line(allday) == "🗑️ שלישי 27/10 · כל היום · יום ספורט"
long_id = {**timed, "id": "x" * 70}  # מזהה ארוך מדי לכפתור טלגרם (64 בתים) — בלי כפתור, לא קריסה
assert [b["callback_data"] for b in c.delete_buttons([timed])] == ["del:abc"]
assert [b["callback_data"] for b in c.delete_buttons([timed, allday])] == ["del:abc", "del:def"]
assert c.delete_buttons([long_id]) == []

# ↔️ טווח שעות נשאר 08:00–10:00 גם בשורה בעברית (בלי זה טלגרם הציג 10:00–08:00)
assert c.span("08:00", "10:00") == "\u206608:00–10:00\u2069"

# 🔤 שגיאות כתיב: מילה אחת לא תואמת בשאילתה של 3+ מילים, או מילה קרובה ("מחשביים")
assert c.matches("מבנה מחשבים מלר", "מבנה מחשבים (הרצאה) - בוסני רם")
assert c.matches("מבנה מחשביים", "מבנה מחשבים (הרצאה)")
assert c.matches("הנדסת חשמלל", "הנדסת חשמל ב' (הרצאה)")
assert not c.matches("הנדסת חשמל", "חשמל ביתי")       # שתי מילים — שתיהן חייבות להתאים
assert not c.matches("מבנה נתונים מלר", "מבנה מחשבים")  # רק מילה אחת מתוך שלוש

# ✅ כל פריט שנוסף — עם יום ותאריך מדויקים
assert c.day_label("26/10/2026") == "שני 26/10"

# 💰 טקסט והקלטה — מודל זול; תמונה — המודל החזק (קורא טבלאות וצילומי מסך)
assert c.models_for(image=False) == ["gpt-5-mini", "gpt-4o-mini"]
assert c.models_for(image=True) == ["gpt-5", "gpt-4o"]

# קובץ תמונה מזוהה גם כשטלגרם שולח mime_type כללי (כך זה הגיע מהמק)
import webhook  # noqa: E402
assert webhook.is_image({"mime_type": "application/binary", "file_name": "5a21.JPG"})
assert webhook.is_image({"mime_type": "image/png", "file_name": "x"})
assert not webhook.is_image({"mime_type": "application/pdf", "file_name": "a.pdf"})

print("ok")
