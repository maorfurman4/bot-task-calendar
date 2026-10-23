# ✅ משימות ליומן

בוט טלגרם שהופך הודעות חופשיות למשימות ולאירועים.

## איך משתמשים
כותבים לבוט בטלגרם בחופשיות, למשל: "לקנות חלב, אימון מחר ב-6 בבוקר".
- **משימה בלי שעה** → Google Tasks
- **אירוע עם שעה** → Google Calendar (שעה כברירת מחדל, תזכורת 30 דק' לפני)

הבוט לא עונה מיד — הוא אוסף את ההודעות פעמיים ביום, מעבד עם GPT-4o, ושולח אישור על מה שנוסף.

## מתי
פעמיים ביום (GitHub Actions, `.github/workflows/task_sync.yml`). הרצה ידנית: Actions → "Task & Calendar Sync" → Run workflow.

## קבצים
| קובץ | תפקיד |
|---|---|
| `task_sync.py` | כל הלוגיקה |
| `requirements.txt` | ספריות פייתון |
| `.github/workflows/task_sync.yml` | התזמון |
| `.github/workflows/keepalive.yml` | שומר שגיטהאב לא יכבה את התזמון |

## סודות (Settings → Secrets → Actions)
`TELEGRAM_TOKEN`, `CHAT_ID`, `OPEN_API_KEY`, `GOOGLE_SERVICE_ACCOUNT_JSON`, `GOOGLE_TASKS_CREDENTIALS`
