# Manga AI agent (MVP)

Telegram-first Uzbek personal and team assistant. It supports task reminders, an end-of-day check-in, a weekly summary, employee-submitted reports, RSS/Atom competitor/news monitoring, AI post drafts, Telegram channel publishing after human approval, admin-directed bot messages, and an optional scheduled phone alarm through Twilio.

## Start

1. Create a Telegram bot with [@BotFather](https://t.me/BotFather). Add it to your channel as an administrator if you want to publish posts.
2. Copy `.env.example` to `.env` and set `TELEGRAM_BOT_TOKEN` and your numeric `ADMIN_CHAT_ID`. Start the bot and send `/start` from the admin account to identify/use the chat ID (or use a Telegram ID lookup bot).
3. Install and launch:

   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
   pip install -r requirements.txt
   python bot.py
   ```

4. Optional integrations:
   - Set `OPENAI_API_KEY` to enable `/draft topic`.
   - Set `TELEGRAM_CHANNEL_ID` (for example `@my_channel`) to enable channel publishing. The bot must be a channel admin.
   - For the alarm call, configure a Twilio account, verified/allowed destination, `ALARM_PHONE`, `TWILIO_FROM_NUMBER`, `TWILIO_ACCOUNT_SID`, and `TWILIO_AUTH_TOKEN`.
   - Add comma-separated RSS/Atom feed URLs to `COMPETITOR_FEEDS`. The bot checks them every 15 minutes. It can only monitor feeds you provide; it does not bypass channel privacy or access restrictions.

## Telegram commands

- `/task 14:30 | Mijozga qo‘ng‘iroq qilish` — add a reminder. `HH:MM` means the next upcoming time; `YYYY-MM-DD HH:MM` sets a date.
- `/tasks` — list pending tasks; `/done ID` — mark a task complete.
- `/checkin Bajardim: ...; Qoldi: ...; Sabab: ...` — record your end-of-day reflection.
- `/weekly` — show task, check-in, and employee-report counts for the last week.
- `/report Ism | Hisobot matni` — employee-submitted report.
- Admin only: `/draft mavzu`, `/approve ID`, `/publish ID`, `/send chat_id | xabar`.

## Important behavior and limitations

- Posts are drafts first and require admin action before publication; there is no unattended auto-posting or unreviewed message-sending. `/approve` records the review step; `/publish` is the separate explicit publish action.
- `/send` sends as the Telegram bot, not as your personal account. The recipient must have started the bot or otherwise be reachable by it. Use only with appropriate authorization and disclose when messages are automated.
- Employee summaries are based on reports they submit to the bot; this MVP does not silently monitor staff. Agree on reporting and data retention with employees.
- Competitor monitoring is RSS/Atom only. The MVP does not generate images, analyze subscriber growth, or read private Telegram channels. Those require additional approved integrations and a content strategy.
- The weekly dashboard is a simple count-based baseline, not a fully AI-generated performance evaluation. Validate recommendations before acting on them.
- SQLite is suitable for a small single-instance deployment. Back up `manga.sqlite3`; use a managed database and process supervisor for production.

## Deployment

Run this process continuously on a server with a persistent disk. Keep `.env` and the SQLite database private, and never commit real tokens or phone credentials. Set `TIMEZONE`, `MORNING_TIME`, and `EVENING_TIME` to your preference. Scheduled times use the configured timezone.
