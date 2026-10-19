# Online Python Coding Test Platform

A Django-template assessment platform with student accounts, staff-controlled tests and questions, server-timed attempts, automatic MCQ grading, results, and leaderboards. SQLite is used by default for local development; configure MySQL with environment variables for MySQL-backed deployments. Coding answers are never run in Django: a separately hosted, restricted execution service must be configured before they can be evaluated.

## Project structure

```text
online_test_platform/   Django settings and root URLs
accounts/               Registration, authentication, student profile
assessments/             Tests, questions, attempts, submissions, scoring
results/                 Result history and leaderboard
dashboard/               Staff and student dashboards
templates/               Shared and feature templates
static/                  Responsive CSS and browser-side timer
requirements.txt         Python dependencies
.env.example             Environment configuration template
```

## Data model

`User 1—1 StudentProfile`; `Test 1—N Question`; `Question 1—N Option` and `Question 1—N CodingTestCase`; `User 1—N TestAttempt`; `Test 1—N TestAttempt`; `TestAttempt 1—N Submission`; each submission points to its question and may select an option. Attempts have a per-student attempt number, server deadline, status, and score. The database enforces unique per-attempt question submissions, unique attempt numbers, and unique profile/user links.

Correct option flags and hidden test cases are only included in staff forms/admin, never in student assessment templates or JSON.

## Installation and local setup

1. Install Python 3.11+ and (for MySQL) a MySQL server.
2. Create and activate a virtual environment, then run `pip install -r requirements.txt`.
3. Copy `.env.example` to `.env` and set a private `DJANGO_SECRET_KEY`. By default the app uses SQLite. For MySQL, create the database and a least-privilege application account, then set `DB_ENGINE=mysql`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, and `DB_PORT`:

   ```sql
   CREATE DATABASE online_test_platform CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
   CREATE USER 'otp_user'@'%' IDENTIFIED BY 'use-a-long-random-password';
   GRANT ALL PRIVILEGES ON online_test_platform.* TO 'otp_user'@'%';
   ```

   Restrict the database account's host/network access to the application host where your provider supports it. Keep real credentials in `.env` or the deployment provider's secret manager only.
4. Run `python manage.py migrate`, `python manage.py createsuperuser`, and `python manage.py runserver`.
5. Sign in to `/django-admin/` with the superuser account and create tests and questions. Newly registered users are always students; staff permissions are granted only by a trusted administrator.

Run tests with `python manage.py test`.

## Assessment workflow

Students register at `/accounts/register/`, sign in, open a published test, and submit once. A test deadline is stored on the server and enforced during answer saves and final submission; browser refreshes do not reset it. MCQs are scored server-side, and answer explanations/correctness are only shown when results are released. `allow_retakes` controls whether additional attempts are permitted. Output comparison trims outer whitespace and trailing whitespace at each line; line breaks and internal whitespace remain significant. Leaderboard ties use earliest completion, followed by username for a deterministic order.

## Isolated Python evaluation

The app intentionally does not execute submitted code locally. Set `CODE_RUNNER_URL` to a trusted, separately deployed sandbox API implementing the JSON contract in `assessments/services.py`, and set `CODE_RUNNER_TOKEN` on the server only. The service must enforce CPU, memory, time, filesystem, process, and network restrictions. With no runner configured, coding questions are visibly unavailable for evaluation and receive no fabricated marks.

The runner accepts `POST` JSON `{"language":"python","source":"...","test_cases":[{"input_data":"...","expected_output":"..."}]}` with server-side bearer authentication and returns JSON `{"outputs":["..."]}` in the same order. It must execute each case in an isolated restricted environment and enforce its own resource/time limits. The interactive Run Code action sends only the public sample case; grading sends configured cases without returning hidden inputs or outputs to the browser.

Sign-in locks out a username/IP pair for five minutes after five failed attempts, and interactive sample runs are throttled per student. Django's default local-memory cache is process-local; configure a shared cache or provider-level rate limiting before scaling to multiple application workers.

## Production deployment

- Set `DJANGO_DEBUG=False`, a cryptographically random `DJANGO_SECRET_KEY`, and production `DJANGO_ALLOWED_HOSTS`.
- Configure MySQL credentials as environment variables; do not commit `.env`.
- Install dependencies, run `python manage.py migrate`, then `python manage.py collectstatic --noinput`.
- Serve the WSGI app with `gunicorn online_test_platform.wsgi:application`; terminate TLS at the hosting provider/reverse proxy and enable secure cookies.
- Provision and configure the isolated code runner separately only if coding evaluation is required.
- Push source with Git to GitHub and connect the repository to a Django-compatible host. Configure the environment variables and MySQL service in the host dashboard, deploy, migrate, collect static assets, and verify health and login flows. Deployment is not performed by this repository setup.

### GitHub and Render deployment outline

To publish from a new local checkout, create an empty GitHub repository, then run:

```powershell
git init
git add .
git commit -m "Build online Python assessment platform"
git branch -M main
git remote add origin https://github.com/<your-account>/<your-repository>.git
git push -u origin main
```

On Render (or another Django-compatible host), connect that repository, use `pip install -r requirements.txt` as the build command and `python manage.py migrate && python manage.py collectstatic --noinput` as the release command. Start with `gunicorn online_test_platform.wsgi:application`. Provision a compatible managed MySQL database separately, configure all `DB_*` settings and `DJANGO_*` secrets in the host's environment dashboard, and use the deployed host name in `DJANGO_ALLOWED_HOSTS`. Create the first administrator with the provider's one-off shell using `python manage.py createsuperuser`. Configure HTTPS and production environment values before exposing the service publicly. `check --deploy` reports that HSTS preload is opt-in; enable `DJANGO_HSTS_PRELOAD=True` only after confirming every subdomain will remain HTTPS-only.

### Vercel deployment

The repository includes `vercel.json`; Vercel detects `manage.py` and the WSGI application at `online_test_platform/wsgi.py`. It collects and serves static files during deployment. PyMySQL is used as the MySQL driver to avoid a native MySQL client build dependency.

1. Push this repository to GitHub and import it from the Vercel dashboard (**Add New → Project**). Keep Vercel's automatic Django build settings; no custom build command is needed.
2. Provision a **separate, persistent MySQL database** reachable from Vercel Functions. Vercel's filesystem is ephemeral and must not be used for SQLite or application uploads.
3. Add these environment variables in Vercel for Production (and Preview if needed):

   ```text
   DJANGO_DEBUG=False
   DJANGO_SECRET_KEY=<a newly generated, private random secret>
   DJANGO_ALLOWED_HOSTS=<your production domain, e.g. my-app.vercel.app>
   DJANGO_CSRF_TRUSTED_ORIGINS=https://<your production domain>
   DB_ENGINE=mysql
   DB_NAME=<database name from MySQL provider>
   DB_USER=<database user>
   DB_PASSWORD=<database password>
   DB_HOST=<database host>
   DB_PORT=<database port>
   DB_CONN_MAX_AGE=0
   ```

   Never commit these values. Vercel's `VERCEL_URL`, `VERCEL_BRANCH_URL`, and `VERCEL_PROJECT_PRODUCTION_URL` are added as allowed hosts/CSRF origins automatically. Add any custom domain to both Django host/origin variables. Vercel deployments fail fast if production mode or MySQL configuration is missing instead of silently falling back to temporary SQLite.
4. Deploy. To apply migrations, install the Vercel CLI and pull the **Production** environment locally:

   ```powershell
   npm install --global vercel
   vercel login
   vercel link
   vercel env pull .env.local --environment=production
   .\.venv\Scripts\python.exe manage.py migrate
   .\.venv\Scripts\python.exe manage.py createsuperuser
   ```

   Run these commands only from a trusted computer; `.env.local` is git-ignored. They connect to the production MySQL database, so use them intentionally. Do not run database migrations during every Vercel build.
5. Check the deployment URL, sign in to `/django-admin/`, and verify registration, database writes, and static assets. Configure an isolated code runner separately if Python answers should be evaluated.

Vercel is a serverless runtime: database connections default to non-persistent (`DB_CONN_MAX_AGE=0`), while login and code-run throttling use Django's local-memory cache and therefore are not shared across function instances. Use a shared cache or provider-level rate limiting before relying on those throttles at scale.
