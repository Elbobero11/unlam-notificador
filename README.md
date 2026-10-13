# UNLaM date reminder

Once a day a GitHub Actions job reads `events.json`. The job is started by an external scheduler (cron-job.org, see "Daily trigger" below) because GitHub's own `schedule:` trigger was delayed by hours in this repo.

- **Reminders:** if an event **starts exactly 5, 3 or 1 days from today**, it posts it to a Discord channel. If nothing matches, it sends nothing.
- **Cleanup:** events whose end date has passed are removed from `events.json` and the change is committed back to the repo. It only commits on days when something was removed.
- **"No real events left" alert:** while **only the placeholder event** is left in `events.json`, a message is sent to a **second** Discord channel, right away and then **every 10 days** (see below).

## Setup

1. **Discord webhook:** in your server, open *Channel settings → Integrations → Webhooks → New Webhook*, pick the channel, and copy the webhook URL.
2. **GitHub secret:** create a repository (private is recommended), push these files, then go to *Settings → Secrets and variables → Actions → New repository secret*. Name it `DISCORD_WEBHOOK_URL` and paste the URL.
3. **Second channel (alert):** create another webhook in the other channel and save it as a second secret named `DISCORD_WEBHOOK_URL_2`.
4. **Workflow permissions:** the cleanup step pushes a commit. If the push fails with a permissions error, open *Settings → Actions → General → Workflow permissions* and select *Read and write permissions*.
5. **Test:** open the *Actions* tab → *UNLaM date reminder* → *Run workflow*. It only posts when an event starts 5, 3 or 1 days away. To see the message right away, temporarily change `--remind-days 5,3,1` in the workflow to include more days (for example `0,1,2,3,4,5,6,7,8,9,10`).

## Daily trigger (cron-job.org)

The workflow has no `schedule:` of its own. A free [cron-job.org](https://cron-job.org) job calls the GitHub API once a day, which does the same as the *Run workflow* button.

1. **Token:** in GitHub, *Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token*. Set *Repository access* to **only this repository** and, under *Repository permissions*, set **Actions** to *Read and write*. Pick the longest expiration offered and write down the date: when it expires, the daily trigger stops until you make a new token and paste it into cron-job.org.
2. **Job on cron-job.org:**
   - URL: `https://api.github.com/repos/YOUR_USER/YOUR_REPO/actions/workflows/notify.yml/dispatches`
   - Schedule: every day at the hour you want, with the time zone set to `America/Argentina/Buenos_Aires`.
   - Advanced: method `POST`, request body `{"ref":"main"}`, and these headers:
     ```
     Accept: application/vnd.github+json
     Authorization: Bearer YOUR_TOKEN
     X-GitHub-Api-Version: 2026-03-10
     ```
3. **Test:** use the test-run button on cron-job.org. A 2xx answer means GitHub accepted it, and a run with the event `workflow_dispatch` should appear in the *Actions* tab within seconds. A 401/403 means the token or its permissions are wrong, and a 404 usually means a typo in the user, repository or file name (or a token without access to the repository).

Reminders are decided by the date in Argentina, so avoid a trigger time very close to midnight.

## Loading the dates from a Google Sheet

A second workflow, `sync_sheet.yml`, copies the sheet into `events.json` once a day. **The sheet is the place where you edit**; manual changes to `events.json` are overwritten on the next sync.

- Only rows whose **start date is after today** are loaded (`start > today`). Rows in the past, or starting today, are skipped and the sheet is never modified.
- `events.json` becomes: the rows from the sheet + the placeholder event. If no future rows are left, only the placeholder remains and the "no real events left" alert logic takes over as before.
- If the sheet can't be read (not shared, link wrong, Google down, a row with an invalid date), the run **fails, GitHub emails you, and `events.json` stays as it was**.

**Sheet layout** (first row = headers; order doesn't matter, extra columns are ignored):

| section | activity | start | end | note | role |
|---|---|---|---|---|---|
| Instancia Diciembre 2026 | Exámenes | 2026-12-10 | 2026-12-19 | | Estudiantes |

`end`, `note` and `role` can be empty (no `end` = single day). Spanish headers also work (`seccion, actividad, inicio, fin, nota, rol`). Dates can be `2026-12-10` or `10/12/2026` (day/month/year). To avoid surprises, format the date columns as plain text or as *Format → Number → Custom date and time → `yyyy-mm-dd`*.

**Setup**

1. In the sheet: *Share → General access → Anyone with the link → Viewer*, or *File → Share → Publish to web → choose the tab and "Comma-separated values (.csv)"*.
2. CSV link: for a shared sheet use `https://docs.google.com/spreadsheets/d/SHEET_ID/export?format=csv&gid=TAB_ID` (the `gid` is in the browser URL); for a published one use the link Google gives you.
3. Save it as a repository secret named `SHEET_CSV_URL`. Anyone with that link can read the sheet, so keep it as a secret.
4. Run *Sync events from Google Sheet* by hand once from the Actions tab and check the log (`Sheet read: N event(s)...`).
5. **Second cron-job.org job:** same as the first one, but the URL ends in `.../workflows/sync_sheet.yml/dispatches` (same body and headers, same token). Schedule it **before** the reminder job, for example 15:00 if reminders run at 15:30.

Both workflows share a lock (`concurrency: events-file`), so they never edit `events.json` at the same time.

Local test: `python sync_sheet.py --csv-file sheet.csv --file events.json --dry-run --today 2026-10-13`

## Pinging a role

An event can ping a Discord role when its reminder is sent. Add an optional `"role"` to the event in `events.json`:

```json
{"section": "Instancia Diciembre 2026", "activity": "Exámenes", "start": "2026-12-10", "end": "2026-12-19", "note": "", "role": "Estudiantes"}
```

Discord only pings a role when the message contains its **numeric ID**, and a webhook can't look a role up by name. So the name goes through a small mapping file, `roles.json`:

```json
{
  "Estudiantes": "123456789012345678",
  "Docentes": "987654321098765432"
}
```

1. **Get the role ID:** in Discord open *User Settings → Advanced* and turn on **Developer Mode**. Then *Server Settings → Roles*, right-click the role and choose **Copy Role ID**. (Another way: type `\@RoleName` in a channel and send it; Discord shows `<@&123…>`, and the digits are the ID.)
2. **Add it to `roles.json`** as `"Name": "ID"`. Names are not case-sensitive, and a leading `@` is ignored.
3. **Use the name** in the event's `"role"`. You can also put the ID there directly and skip `roles.json`.

How it behaves:

- The mention is added at the end of that event's line, e.g. `Exámenes · _Instancia Diciembre 2026_ @Estudiantes`, and only when that reminder is sent.
- Only the roles mentioned in the message are pinged. `@everyone`, `@here` and individual users never are, even if that text appears in an event name or note.
- An unknown role, a typo, a broken `roles.json` or a role ID that doesn't look valid never blocks the reminder. The message goes out without the ping and the Actions log shows a `WARNING` saying why.
- Events without `"role"` work as before. The cleanup keeps the field, and the placeholder event can have one without changing how it is detected.
- If the ping shows the role name but doesn't notify its members, check the role's settings in Discord: *Server Settings → Roles → the role → Display → Allow anyone to @mention this role*. Discord's documentation doesn't say whether webhooks need that, so I haven't been able to confirm it either way.

## Changing the reminder days

Edit `--remind-days 5,3,1` in `.github/workflows/notify.yml`. For example `7,2,0` reminds a week before, two days before, and on the day itself.

## Updating the dates

`events.json` is a plain list. Each item looks like:

```json
{"section": "Instancia Diciembre 2026", "activity": "Exámenes", "start": "2026-12-10", "end": "2026-12-19", "note": ""}
```

Dates can be written `2026-10-03` or `2026-10-3`. An optional `"role"` pings a Discord role (see "Pinging a role"). You can edit the file by hand, or regenerate it from a saved copy of the calendar page:

```
pip install requests beautifulsoup4
python unlam_calendar.py --file calendario.html --all --json > events.json
```

Events that already ended are cleaned up automatically, but new dates have to be added by you.

## The placeholder event and the second-channel alert

Keep this filler event in `events.json` at all times. It starts in 2050, so it never triggers reminders and is never cleaned up:

```json
{"section": "Instancia", "activity": "Nombre de evento", "start": "2050-1-1", "end": "2050-1-2", "note": ""}
```

The alert is considered on every run in which, after the cleanup, the only element in `events.json` is exactly that placeholder (same section, activity, note and dates). It is **sent**:

- **right away** the first time (no alert has ever been sent), and **on the day the cleanup removes events** (for example the last real ones), even if an alert went out recently;
- otherwise **once every 10 days**, counted from the last alert that was really sent.

The message mentions how many ended events were removed when there were any.

**How it remembers:** after a successful send, the workflow saves the date in `alert_state.json` and commits it. If the send fails (missing secret, Discord down), nothing is saved, GitHub emails you about the failed run, and the next daily run tries again. If `alert_state.json` is missing or unreadable, it is treated as "no alert sent yet".

**Changing the interval:** edit `--alert-every 10` in the "Remove ended events" step of `.github/workflows/notify.yml` (`0` means every run). **To test it again** before the interval passes, delete `alert_state.json` from the repo (or set `--alert-every 0` for the test) and run the workflow manually.

It does not fire if any other event remains in the file, or if the placeholder is missing from the file.

Note: with the placeholder present, the log warning "has no events on or after today" never appears, because the placeholder is always in the future. The second-channel alert replaces it.

The alert text is in Spanish and lives in `build_placeholder_alert()` in `notify.py`. The placeholder definition is `PLACEHOLDER` in the same file.

## Local tests

```
python notify.py --dry-run --today 2026-10-02                # show today's reminder message
python notify.py --prune-only --dry-run --today 2026-10-02   # show what cleanup would remove
python notify.py --placeholder-alert --removed 3 --dry-run   # show the alert text
python notify.py --prune-only --dry-run --alert-every 10     # would the "no real events left" alert go out today?
```
