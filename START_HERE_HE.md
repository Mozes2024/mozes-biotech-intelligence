# v2D — התחלה מהירה להעלאה ידנית

חבילה זו מעדכנת את MOZES Biotech Catalyst Intelligence ל־v2D בלי לשנות קבצי
`.github/workflows` ובלי להפעיל Actions מהסביבה שבה נבנתה החבילה.

## סדר העלאה מומלץ

1. פתחו את המאגר `Mozes2024/mozes-biotech-intelligence` בענף `main`.
2. העלו את תוכן החבילה לשורש המאגר והחליפו קבצים קיימים.
3. מחקו מהמאגר את `web/index_v2.html`, `web/data_v2.json`, ושלושת קובצי
   `__pycache__/*.pyc` המפורטים ב־`MANIFEST.json` אם הממשק אינו מזהה מחיקות.
4. ודאו שה־diff אינו מכיל `.github/workflows`; חבילה זו אינה כוללת תיקוני workflow.
5. בצעו commit אחד עם הודעת `v2D consolidation and validation foundation`.
6. המתינו להרצת ה־CI הרגילה היחידה ב־Python 3.11. אין צורך להפעיל ריצות נוספות.

## לאחר ההעלאה

הריצו מקומית: `pip install -e ".[dev]"`, אחר כך `pytest -q`, ולבסוף
`mozes validation-evaluate`. הפקודה האחרונה מדווחת ושומרת metrics בלבד; היא
אינה פותחת RUN-UP או HOLD. שימוש רגיל בממשק: `mozes app --port 8000`.

## מגבלות ידועות

מזהי רענון ברקע נשמרים בזיכרון תהליך השרת, בעוד תוצאות המקור נשמרות ב־SQLite
ב־`refresh_runs`. לאחר אתחול השרת אפשר לראות את תוצאת הרענון האחרונה בממשק,
אך אי אפשר להמשיך polling של מזהה ריצה ישן. שערי המסחר נשארים נעולים כברירת
מחדל ודורשים החלטת enable מפורשת מחוץ ל־evaluator.
