# Meet Notes Auto-Joiner

A small self-hosted tool for one person, Aqeel. It watches one Google Calendar. A few minutes before each event that Aqeel organizes and that has a Google Meet link, it opens that link in a Chromium profile signed in as Aqeel's own Workspace account, joins with the microphone and camera off, and starts Gemini **Take notes for me** as soon as it is in the call. It confirms the notes control, then leaves. It does not wait for someone else to arrive.

The joining account is Aqeel's own Google account. The app's only Google API scope is `calendar.readonly`. It lists events. It does not create, edit, or delete events, and it does not touch Gmail or Drive. The browser is used for two things: a one-time sign-in, and joining or leaving Meet.

Tests do not sign in to Google and do not join a real Meet. Live testing is a separate step, at the bottom of this file, and needs Aqeel's approval.

## Quick start on a Mac (end to end)

Do this from a checkout that already has a virtualenv (`.venv`), `pip install -e ".[dev]"`, and `playwright install chromium`. The longer notes for each step are linked.

1. **OAuth client JSON.** Download a Desktop-app client and save it at `~/.config/meet-notes/credentials.json`. `chmod 600` that file. See [Google Cloud OAuth setup](#google-cloud-oauth-setup).
2. **auth.** `meet-notes auth`. The read-only Calendar token lands at `~/.config/meet-notes/token.json`.
3. **login.** `meet-notes login`. Sign the Chromium profile in as Aqeel. This is separate from the Calendar token.
4. **config.** `mkdir -p ~/.config/meet-notes && cp config.example.yaml ~/.config/meet-notes/config.yaml`. Leave it there. That path is the default lookup, so `meet-notes` works from any folder. Turn on `notify` for Gmail (host `smtp.gmail.com`, port 587, `smtp_ssl: false`, or port 465 with `smtp_ssl: true`). See [Email](#email).
5. **Gmail app password file.** Create an app password in the Google account (not the normal Gmail password) and store it only in `~/.config/meet-notes/smtp_password` with mode `600`. Do not put it in the YAML file.
6. **test-email.** `meet-notes test-email`. One message should arrive. Fix SMTP before relying on meeting mail.
7. **launchd.** Follow [Run in the background on macOS (launchd)](#run-in-the-background-on-macos-launchd) so `run` starts at login and comes back after a crash.

## Do Gemini notes keep going after this account leaves?

Yes. On 28 October 2026 Aqeel confirmed that **Take notes for me** keeps running for the whole meeting after the account that started it leaves.

That matches how Google describes the feature in the [computer help article](https://support.google.com/meet/answer/14754931) (checked the same day): it is a meeting control, people in the call are told, a pencil icon is shown, and stopping notes is a separate Stop action. The notes Doc is written after the meeting ends, in the organizer's Drive. The article itself does not say what happens when the starter leaves. The owner's confirmation is why the bot does not stay in the call.

The default is `stay_mode: leave_after_notes`. The bot still checks the on-screen notes control, logs the signal it saw, and then leaves.

The other modes are optional. Use one only if a later check shows that notes stopped when this account left, or if you explicitly want the account to remain in the call:

| Value | Behaviour |
| --- | --- |
| `leave_after_notes` | Leave as soon as notes look active. Default. |
| `until_alone` | Stay until every other participant has gone, then leave. Also leaves at the scheduled end plus `stay_grace_minutes`. |
| `until_meeting_end` | Stay until the scheduled end plus `stay_grace_minutes`. |

## What the bot will not do

- It will not type a Google password. If the profile is signed out, it stops and logs `auth_required`.
- It will not open a link that is not `https://meet.google.com/...`, except in tests, where the allow-list is hard-coded to the local fixture. The allow-list is not a config setting.
- It will not click the consent dialog's **Leave**. Consent is accepted with Start, Join, or Continue. Hanging up uses the separate **Leave call** button.
- It will not join the same meeting instance twice on its own. See [Restart safety](#restart-safety).
- It will not open a second Chromium profile. Overlapping meetings are separate tabs in the one signed-in profile, up to `max_concurrent_joins` (default 4, ceiling 8).
- It will not join a meeting this account did not organize, while `only_organizer_meetings` is true (the default). Those events are recorded as `skipped`.

## Requirements

- Python 3.11 or newer, or Docker.
- A Google Workspace account that can open Meet.
- The **meeting organizer** must have a Workspace edition or Google AI plan that includes Take notes for me. The account that clicks the button must be allowed to see it. [Google's article](https://support.google.com/meet/answer/14754931) lists both limits.
- The Meet UI language should be English. The selectors are English.
- Spoken language for the notes themselves must be one Gemini supports (English, French, German, Italian, Japanese, Korean, Portuguese, or Spanish, per the same article).
- Timezone default is `Asia/Karachi`. Instants are stored in UTC.

## Google Cloud OAuth setup

Do this once in the [Google Cloud Console](https://console.cloud.google.com/) for the Workspace account.

1. Create or choose a project.
2. Enable the **Google Calendar API** (APIs & Services → Library).
3. Configure the OAuth consent screen.
   - User type: **Internal** if this is a Workspace organization. That limits the client to people in the domain.
   - Scopes: add only `https://www.googleapis.com/auth/calendar.readonly`.
   - Do not add Gmail, Drive, or the full Calendar scope.
4. Create an OAuth client ID of type **Desktop app**. A Web client will not work with the loopback login this tool uses.
5. Download the JSON. Put it somewhere outside the repo:

   - macOS and Linux: `~/.config/meet-notes/credentials.json`
   - Windows: `%APPDATA%\meet-notes\credentials.json`

6. `chmod 600` the file on macOS or Linux.

The file contains a client secret. Do not commit it. The repo ignores `credentials.json` and `token.json`.

Find a calendar id under Google Calendar → Settings → the calendar → Integrate calendar. `primary` is the default and is the signed-in user's main calendar.

## Configuration

Copy `config.example.yaml` to `config.yaml` in the working directory, or to `~/.config/meet-notes/config.yaml`. Environment variables override the file. The variable `MEET_NOTES_CONFIG` points at a file.

Paths in the YAML file are relative to that file. Blank paths use the per-user directory above. Docker uses `config.docker.example.yaml`, with paths inside the container.

| Setting | Default | Meaning |
| --- | --- | --- |
| `calendar_id` | `primary` | The one calendar to watch. |
| `timezone` | `Asia/Karachi` | Display and the browser timezone. Scheduling uses absolute instants. |
| `poll_interval_seconds` | `60` | How often to list events. |
| `lookahead_hours` | `48` | How far ahead a poll looks. |
| `join_lead_minutes` | `3` | Join this many minutes before the start. |
| `join_after_start_minutes` | `10` | Still join if the process wakes up this late. After that the row is `missed`. |
| `wait_for_participant` | `false` | When false, start notes as soon as the bot is in the call. When true, wait until someone else is visible. |
| `only_organizer_meetings` | `true` | Skip events whose `organizer.self` is not true. If `self` is missing, compare `organizer.email` with the signed-in account. |
| `nobody_joined_timeout_minutes` | `10` | Used only when `wait_for_participant` is true. |
| `admit_timeout_minutes` | `5` | How long to wait in "Ask to join". |
| `notes_ui_timeout_seconds` | `45` | Cap on how long to wait for the notes control to confirm. At most 60. The bot leaves as soon as notes look active. |
| `meeting_log` | next to the state db | Append-only `meeting-log.csv`. |
| `stay_mode` | `leave_after_notes` | See the table above. |
| `stay_grace_minutes` | `2` | Extra time after the scheduled end for the stay modes. |
| `alone_confirm_seconds` | `20` | How long the bot must be the only person left before `until_alone` hangs up. |
| `skip_declined` | `false` | `false` means every non-cancelled Meet event is in scope, including ones you have not accepted. |
| `skip_all_day` | `true` | All-day events have no clock time to join. |
| `missing_polls_before_cancel` | `2` | If an event disappears from the feed this many successful polls in a row, mark it cancelled. One empty glitch does not. |
| `max_prejoin_attempts` | `3` | Retries only when the Meet page never opened (network failure, signed-out redirect). |
| `max_concurrent_joins` | `4` | How many Meet tabs may be open at once. 1 to 8. |
| `self_display_names` | `You`, `Aqeel` | Exact names that count as this account. Meet usually labels you "You". Set the full Meet display name if it is not "Aqeel". |
| `headless` | `true` | The run loop. `meet-notes login` is always headed. |
| `browser_locale` | `en-US` | Browser locale. The Google account language should still be English. |
| `browser_channel` | `chromium` | Set to `chrome` only if Chrome is installed and you want that channel. |
| `no_sandbox` | `false` | Set true in Docker. The image sets `MEET_NOTES_NO_SANDBOX=true`. |
| `screenshot_on_failure` | `true` | PNG files under the log directory when a visit does not end cleanly. They can show faces and names. |
| `push.enabled` | `false` | Polling always runs. Push is optional and needs a public HTTPS URL. |
| `notify.enabled` | `false` | Send one email after each joined attempt, and one deduped email for a hard failure. |

Push, if you turn it on, also calls `events.watch` and `channels.stop`. Those calls register a notification channel. They do not change events. Set `push.public_url` to the HTTPS URL Google will POST to, and set `push.channel_token` to a long random string (`MEET_NOTES_PUSH_CHANNEL_TOKEN`). Put a TLS reverse proxy in front of `push.listen_host` / `push.listen_port` (default `127.0.0.1:8766`). A laptop behind NAT should leave push off.

Useful environment variables: `MEET_NOTES_CALENDAR_ID`, `MEET_NOTES_TIMEZONE`, `MEET_NOTES_JOIN_LEAD_MINUTES`, `MEET_NOTES_STAY_MODE`, `MEET_NOTES_HEADLESS`, `MEET_NOTES_PROFILE_DIR`, `MEET_NOTES_CREDENTIALS_FILE`, `MEET_NOTES_TOKEN_FILE`, `MEET_NOTES_STATE_DB`, `MEET_NOTES_LOG_DIR`, `MEET_NOTES_SKIP_DECLINED`. Booleans are `true` or `false`. `MEET_NOTES_SELF_DISPLAY_NAMES` is a comma-separated list.

Unknown YAML keys are an error, so a typo does not get ignored.

## First-time login

Two separate sign-ins. Both stay outside the repo.

### 1. Calendar token

```bash
meet-notes auth
```

A browser opens Google's consent screen. Accept the read-only Calendar scope. The token is saved at `~/.config/meet-notes/token.json` (or `%APPDATA%\meet-notes\token.json`). On a server, add `--no-browser --oauth-port 8765` and open the printed URL through an SSH tunnel:

```bash
ssh -L 8765:127.0.0.1:8765 user@the-server
```

The simpler path is to run `meet-notes auth` on the laptop and copy `credentials.json` and `token.json` to the server. `chmod 600` both files.

### 2. Meet browser profile

The profile has to be created by this app's Chromium, not by the everyday Chrome profile.

```bash
meet-notes login
```

Sign in as the Workspace user that should join meetings. When the account page is visible, press Enter, then Enter again to save. The profile is the directory `browser-profile` next to the token. It contains session cookies. Treat it like a password. Do not commit it or paste it into chat.

Check that a session cookie was stored, without calling Google:

```bash
meet-notes check-profile
```

A cookie only means a session was saved. Google can still reject it later, especially after the profile moves to a new IP.

## Run locally

### macOS or Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium
cp config.example.yaml config.yaml
# put credentials.json in ~/.config/meet-notes/ first
meet-notes auth
meet-notes login
meet-notes plan
meet-notes run
```

`plan` reads the calendar and prints what is due. It does not open Meet. `run` joins.

Stop with Ctrl+C. If a call is open, the process tries to leave before it exits.

### Windows (PowerShell)

```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
playwright install chromium
copy config.example.yaml config.yaml
meet-notes auth
meet-notes login
meet-notes plan
meet-notes run
```

`tzdata` is installed as a dependency so `Asia/Karachi` works on Windows.

### Capacity

Meetings that start together, or overlap, are joined at the same time. Each one is a tab in the single signed-in Chromium profile. Chromium locks that profile, so the app does not launch a second copy of it and does not clone it per meeting.

`max_concurrent_joins` defaults to 4 and cannot be set above 8. That ceiling is there so a typo cannot open dozens of Meet tabs. A meeting that does not get a free tab stays scheduled and is started as soon as a tab frees, if its late-join window is still open. If the window closes first, the row is `missed` and the browser is not opened for it.

Plan on about 1 GB for Chromium itself and roughly 0.5–1 GB more for each Meet tab. Those are sizing numbers, not a measurement from a live call (this repo does not join a real Meet in tests).

- With the default `leave_after_notes` behaviour each tab lives only about a minute (join, start notes, leave), so a cap of 4 is fine on an 8 GB Mac.
- Set `max_concurrent_joins` lower if the machine starts swapping. Swapping during a join makes the Meet UI miss the timeout. A stay mode that remains in the call for the whole meeting holds that tab the whole time, and that is the case that needs more RAM.

### Docker, laptop or EC2

```bash
mkdir -p .meet-notes/secrets .meet-notes/profile .meet-notes/data
cp config.docker.example.yaml .meet-notes/config.yaml
cp /path/to/credentials.json .meet-notes/secrets/credentials.json
# token.json and the browser profile are easiest to create on the laptop, then copy:
#   .meet-notes/data/token.json
#   .meet-notes/profile/   (the Chromium user-data directory)
chmod 600 .meet-notes/secrets/credentials.json .meet-notes/data/token.json
chown -R 1000:1000 .meet-notes
docker compose build
docker compose run --rm meet-notes plan
docker compose up
```

The container runs as uid 1000. Override with `MEET_NOTES_DOCKER_UID` and `MEET_NOTES_DOCKER_GID` if the files are owned by someone else. `plan` and `run` need the token and the profile already in those directories.

Do the interactive Google sign-in on the laptop. Copying the profile into Docker is the path that does not need a display inside the container.

## Run in the background on macOS (launchd)

This is the setup on Aqeel's Mac. The job is a LaunchAgent in the GUI session, so it runs only while that user is logged in.

1. **Config.** Keep `~/.config/meet-notes/config.yaml`. That is the default lookup, so `meet-notes` works from any folder. Do not rely on a `config.yaml` sitting in the repo.

2. **Command on PATH.** From the repo:

   ```bash
   ln -sf "$PWD/.venv/bin/meet-notes" /opt/homebrew/bin/meet-notes
   ```

   On an Intel Mac, Homebrew's prefix is `/usr/local`, so use `/usr/local/bin/meet-notes` instead. The symlink is for the terminal. The LaunchAgent below calls the virtualenv binary by its absolute path, so it does not depend on PATH.

3. **LaunchAgent.** Copy `deploy/com.rev9.meet-notes.plist` to `~/Library/LaunchAgents/com.rev9.meet-notes.plist`. Replace every `YOUR_USER` and the repo path. launchd does not expand `~`, so the plist has to contain absolute paths:

   - program: `/Users/YOUR_USER/<repo>/.venv/bin/meet-notes`
   - arguments: `--config`, `/Users/YOUR_USER/.config/meet-notes/config.yaml`, `run`
   - stdout: `/Users/YOUR_USER/.config/meet-notes/logs/launchd.out`
   - stderr: `/Users/YOUR_USER/.config/meet-notes/logs/launchd.err`

   The template sets `Label` to `com.rev9.meet-notes`, `RunAtLoad` true, `KeepAlive` with `SuccessfulExit` false (restart after a crash, stay stopped after a clean exit), and `ThrottleInterval` 30. Create the log directory first, or launchd has nowhere to write:

   ```bash
   mkdir -p ~/.config/meet-notes/logs ~/Library/LaunchAgents
   ```

   The plist in `~/Library/LaunchAgents` should be owned by you and not writable by other users.

4. **Load, unload, restart, status.**

   ```bash
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.rev9.meet-notes.plist
   launchctl bootout gui/$(id -u)/com.rev9.meet-notes
   launchctl kickstart -k gui/$(id -u)/com.rev9.meet-notes
   launchctl list | grep meet-notes
   ```

   `bootstrap` loads it, `bootout` unloads it, `kickstart -k` restarts it. `launchctl list` prints the last exit code and the PID while it is running. The app's own log is still `~/.config/meet-notes/logs/meet-notes.jsonl`. launchd's copies of stdout and stderr are `launchd.out` and `launchd.err` in that same directory.

5. **The Mac must be awake and logged in.** A GUI LaunchAgent does not run at the login window, and a sleeping Mac misses the join window. For always-on use, either keep the session awake with `caffeinate -dims`, or turn on the Energy setting that prevents sleep while the display is off (System Settings → Energy, or Battery on a laptop: prevent automatic sleeping on power adapter when the display is off).

## Deploy on EC2

Use Ubuntu 24.04. For the default cap of 4, **t3.large** (2 vCPU, 8 GiB) is enough: with `leave_after_notes` each tab lives about a minute. **t3.medium** (4 GiB) is tight. Use a larger instance if several meetings will use `until_meeting_end` at the same time. Open outbound HTTPS. You do not need an inbound port unless you enable Calendar push. Outbound SMTP is needed only if email is enabled.

Docker's shared memory is 2 GB (`shm_size` in `docker-compose.yml`). Chromium is also started with `--disable-dev-shm-usage`, so it can use the container's `/tmp`. Leave a few GB free on the data volume for the profile, the state database, and failure screenshots.

Docker is the path to use. The systemd unit `deploy/meet-notes-docker.service` runs `docker compose up` in the foreground and restarts on failure.

```bash
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2
sudo usermod -aG docker "$USER"
# log out and back in so the group applies
sudo mkdir -p /opt/meet-notes
sudo chown "$USER" /opt/meet-notes
# copy this repo to /opt/meet-notes, then the .meet-notes directory as above
cd /opt/meet-notes
docker compose build
docker compose run --rm meet-notes plan
sudo cp deploy/meet-notes-docker.service /etc/systemd/system/meet-notes.service
sudo systemctl daemon-reload
sudo systemctl enable --now meet-notes
sudo systemctl status meet-notes
```

`docker compose run` is for one-off commands (`plan`, `status`, `history`). The service owns `up`.

### Bare Python on EC2

`deploy/meet-notes.service` expects a virtualenv at `/opt/meet-notes/.venv` and a user named `meetnotes`.

```bash
sudo useradd --system --create-home --home-dir /var/lib/meet-notes meetnotes
sudo mkdir -p /opt/meet-notes /etc/meet-notes
sudo python3 -m venv /opt/meet-notes/.venv
sudo /opt/meet-notes/.venv/bin/pip install /opt/meet-notes
sudo /opt/meet-notes/.venv/bin/playwright install-deps chromium
sudo -u meetnotes /opt/meet-notes/.venv/bin/playwright install chromium
sudo cp deploy/meet-notes.env.example /etc/meet-notes/meet-notes.env
sudo cp config.example.yaml /etc/meet-notes/config.yaml
# edit the config so profile, token, credentials, state, and logs
# live under /var/lib/meet-notes, which meetnotes can write
sudo cp deploy/meet-notes.service /etc/systemd/system/meet-notes.service
sudo systemctl daemon-reload
sudo systemctl enable --now meet-notes
```

### Copy the profile, or log in with VNC

Copying is the default.

On the laptop, after `meet-notes login`:

```bash
tar -C ~/.config/meet-notes -czf browser-profile.tgz browser-profile
scp browser-profile.tgz credentials.json token.json ec2:
```

On the EC2 host:

```bash
mkdir -p ~/.config/meet-notes
tar -C ~/.config/meet-notes -xzf browser-profile.tgz
install -m 600 token.json credentials.json ~/.config/meet-notes/
meet-notes check-profile
```

For Docker, unpack `browser-profile` so its contents are `.meet-notes/profile/` (the directory that contains `Local State` or `Default/`), and put the token at `.meet-notes/data/token.json`.

A Chromium profile made on macOS sometimes fails on Linux, and Google often challenges a session that changes country or IP. If `run` logs `auth_required`, sign in on the EC2 machine itself:

```bash
sudo apt-get install -y tigervnc-standalone-server
vncserver :1 -geometry 1280x800 -localhost yes
# from the laptop: ssh -L 5901:127.0.0.1:5901 user@ec2
# connect a VNC client to 127.0.0.1:5901
export DISPLAY=:1
meet-notes login --wait-minutes 20
```

Sign in inside the window. The command exits when it sees a Google session cookie, or after 20 minutes. Stop the service before `login`. Chromium will not share a profile with a running `meet-notes run`.

`xvfb` alone is not enough for a sign-in. There is no password to type into a script, and there is no one to approve Google's prompts.

## Commands

| Command | What it does |
| --- | --- |
| `meet-notes auth` | Calendar OAuth. Read-only scope. |
| `meet-notes login` | Headed Chromium sign-in. Saves the profile. |
| `meet-notes check-profile` | Looks for a session cookie locally. No network. |
| `meet-notes plan` | Syncs the calendar and prints the window for each event. Does not join. |
| `meet-notes run` | Polls and joins. |
| `meet-notes run --dry-run` | Syncs and logs, does not claim or open Meet. |
| `meet-notes status` | Local health. Exit 1 if the token, client file, or profile is missing. |
| `meet-notes status --json --strict` | Also exit 1 on warnings. |
| `meet-notes history --limit 20` | Date, title, scheduled start, joined, notes started, left, outcome, and the plain-English reason. |
| `meet-notes requeue EVENT_ID --confirm` | Manual override so a test meeting can be tried again. |
| `meet-notes test-email` | Send one SMTP test message to `notify.to`. |
| `meet-notes version` | Print the version. |

`status` does not call Google. It checks that the files exist, that the state database opens, and whether a session cookie is stored. `--strict` also fails when the last sync stored an error or a meeting was interrupted.

`history` times are in the configured timezone. `notes=yes` includes meetings where notes were already on. The reason line says which one it was. The same fields are in `meeting-log.csv`.

## Logs, history, restart safety

Logs are JSON lines in `logs/meet-notes.jsonl`, rotated at 5 MB, plus a short line on stderr. They include meeting titles and Meet URLs. Screenshots on failure can include the call. Keep that directory private.

The state database is SQLite (`state.db`). One row per Calendar instance id. Recurring events get one row per instance (`eventId_20261028T040000Z` is the usual shape).

A row is claimed only when it is still `scheduled` (or a pre-join failure) and the Meet page has never been opened. Opening the page sets `meet_opened_at`. After that the automatic loop will not join that instance again, including when several meetings are claimed in the same second:

- A clean finish (`left`, `left_alone`, `left_after_end`, `nobody_joined`, `notes_unavailable`, …) is final.
- A crash after the page opened becomes `interrupted` on the next start and is not joined again.
- A crash before the page opened becomes `failed_before_join` and can be tried again, up to `max_prejoin_attempts`.
- Ctrl+C or SIGTERM before the page opens gives the claim back, so the next start can still join once.
- A cancelled event, or an event missing for two polls, is not joined.
- A time change before the page opens moves the row and allows one join at the new time. A time change after the page opened does not cause another join.

Two `run` processes cannot share one database. The second exits because of the lock file next to `state.db`.

`requeue --confirm` clears that history on purpose. Use it only when you know the account is not already in the meeting. The automatic loop never calls it.

## Meeting log

Besides SQLite, each attempt is appended to `meeting-log.csv` (default: next to `state.db`, or `meeting_log` in the config). The file is append-only. One row per attempt:

`date, title, scheduled_start, joined_at, notes_started, notes_started_at, left_at, outcome, reason`

`outcome` is one of `notes_started`, `notes_failed`, `join_failed`, `blocked_by_host`, `signed_out`, or `skipped`. `notes_started` is `yes` or `no`. `meet-notes history` prints those same fields, including the reason. A second poll does not write the row again.

## Email

After a meeting attempt finishes (joined, notes started or failed, left), the bot can send one email. A hard failure such as the browser profile being signed out, or Calendar authentication breaking, sends one more email, and that one is deduped until a later attempt starts notes. Skipped meetings, including "not the organizer", are written to the log and are not emailed.

Email is off unless `notify.enabled` is true. A failed send logs a warning and does not stop the joiner.

The password is read only from `MEET_NOTES_SMTP_PASSWORD` or from `~/.config/meet-notes/smtp_password` with mode `600`. It is not a YAML key and it is not written to the log. If the file is readable by the group or by others, it is ignored.

Zoho, SSL:

```yaml
notify:
  enabled: true
  to: aqeel@example.com
  from: aqeel@example.com
  smtp_host: smtp.zoho.com
  smtp_port: 465
  smtp_ssl: true
  smtp_username: aqeel@example.com
```

Gmail, STARTTLS with an app password (Google Account → Security → App passwords). Do not use the normal Gmail password:

```yaml
notify:
  enabled: true
  to: aqeel@example.com
  from: aqeel@example.com
  smtp_host: smtp.gmail.com
  smtp_port: 587
  smtp_ssl: false
  smtp_username: aqeel@example.com
```

Gmail also accepts port 465 with `smtp_ssl: true`. Then:

```bash
export MEET_NOTES_SMTP_PASSWORD='the-app-password'
meet-notes test-email
```

Or, instead of the environment variable:

```bash
install -m 600 /dev/null ~/.config/meet-notes/smtp_password
# put the password on one line in that file
```

## Known limits

- **Notes continue after this account leaves.** Aqeel confirmed on 28 October 2026 that Take notes for me keeps running for the rest of the meeting after the starter hangs up. `leave_after_notes` is the default because of that. `until_alone` and `until_meeting_end` are overrides for a later Meet change, or for when you want the account to stay. They are not required for the notes to finish.
- **Gemini license.** The organizer's edition has to include Take notes for me. If the button is missing, the bot logs `notes_unavailable`, leaves, and does not retry. An admin can also turn the feature off. Smart features in the account have to be on. Google documents this in the help article linked above.
- **Host controls.** When host management is on, only the host and co-hosts can start or stop notes. If Aqeel is a guest, the control may be missing. The same outcome is logged.
- **Ask to join.** The bot clicks Ask to join and waits `admit_timeout_minutes`. If the host never admits it, the outcome is `blocked_by_host`, the reason says the host did not admit the account, and the bot leaves. It does not knock again.
- **Consent.** Admins can require an explicit Start / Join / Continue before notes, recording, or transcripts. The bot accepts those. It does not click the consent Leave button. A dialog worded in a way the selectors do not know is a failure, with a screenshot if that option is on.
- **Selector drift.** Meet renames buttons often. Every label the bot looks for is in `src/meet_notes/selectors.py`, with fallbacks. The English fixture in `tests/fixtures/meet_page.html` uses the same labels. When a real call cannot find Join, the people list, the notes control, or Leave, update that module and the fixture together and run `pytest`.
- **People count.** The default path does not wait on this count. It is still logged, and `until_alone` still uses it. The bot reads the roster, the tiles, and the people button (including a badge count). If the names only show this account but the button count is higher, the button wins. "You" and `self_display_names` count as this account.
- **Notes already on.** If an admin or the host already started notes, the bot looks for the active-notes signal, logs `already-on`, and leaves without pressing Start.
- **Verification is the UI only.** The bot cannot open the Doc Google creates after the meeting. "Verified" means it saw a Stop taking notes control or an exact Taking notes label. If it clicked Start and that signal never appeared, the outcome is `left_unverified` and it still leaves, unless `stay_mode` is a stay mode and `stay_even_if_unverified` is true.
- **The bot always leaves** when `stay_mode` is `leave_after_notes`, including when notes could not be confirmed. That is so the account does not remain in the call after a UI change.
- **Length.** Google recommends meetings of about 15 minutes to 8 hours for this feature. Leaving early does not shorten the meeting. It only removes this account.
- **Only meetings this account organizes.** `only_organizer_meetings` defaults to true. `organizer.self: true` means join. `self: false` means skip. If Google omits `self`, the organizer email is compared with the primary calendar id. The row, the meeting log, and `plan` / `history` use outcome `skipped` and the reason `not organizer (organizer: <email>)`. No email is sent for that skip.
- **Muted join.** Chromium is launched with fake media devices pointed at a silent wav and a black frame, so a missed toggle cannot open the laptop microphone or camera. The bot turns the Meet mic and camera off before joining and checks again after it is in the call. `mic_off` and `camera_off` are logged from that check.
- **Concurrent cap.** At most `max_concurrent_joins` tabs are open (default 4, ceiling 8). They share one profile. A further meeting waits for a free tab and is still joined if the late-join window is open. It is marked `missed` only when that window closes first. With `leave_after_notes` each tab lives about a minute, so 4 is fine on an 8 GB Mac. See [Capacity](#capacity).
- **Headless sessions.** Google may show a challenge when a profile created on a laptop is used headless on EC2. The bot will not solve that challenge. Sign in again on the EC2 display.
- **All-day events** and Meet links that are only a `https://meet.google.com/new` shortcut are skipped.
- **Push** can be missed. Polling is the source of truth.

## Live test plan

Do this only with a meeting Aqeel set up for the test. Do not point the first run at a real customer meeting.

1. Confirm by hand, once, that the organizer account can start Take notes for me in a Meet and that a Doc appears after that meeting ends.
2. Set the Google account language to English.
3. Put a test event on the watched calendar that Aqeel organizes, starting about 10 minutes out, with a Meet link. Invite one other account that Aqeel controls. An event someone else organizes should show up in `plan` as `skipped` with reason `not organizer (organizer: …)` and the browser should not open it.
4. Run `meet-notes plan` and check the event is listed as too early, then due inside the lead time. Times should read in Asia/Karachi (PKT).
5. Start `meet-notes run` on the machine that has the profile.
6. From the other account, join the test Meet with a camera you are willing to have in a failure screenshot.
7. Watch the bot join with the mic and camera off (`mic_off` and `camera_off` true in the log). It starts notes as soon as it is in the call. It does not wait for the other account. That other account can join after the bot has already left.
8. Confirm the pencil / "notes are on" state. With the default `stay_mode: leave_after_notes`, the bot should leave on its own once that signal is visible, within the notes confirm grace (at most 60 seconds). If the notes control is missing, the log should give a plain reason and the bot should leave instead of sitting in the call.
9. Stay in the meeting and talk for a couple of minutes after the bot leaves. End the meeting.
10. After the meeting, open the notes Doc in the organizer's Drive (Google Meet folder) and on the Calendar event. It should include what was said **after** the bot left, through the end of the meeting. That is the behaviour Aqeel confirmed.
11. If the Doc stops at the bot's leave time, Meet's behaviour has changed. Set `stay_mode: until_alone`, run `meet-notes requeue EVENT_ID --confirm`, and repeat with a new event. Leave the default as it is unless that happens.
12. Cancellation: create an event inside the lead window, then delete it before the join time. `history` should show `cancelled` and the browser should not open.
13. Time change: move an event by 15 minutes before it is opened. `plan` should show the new time, and the bot should join once at the new time.
14. Recurring: two instances on different days. Each instance id should be joined at most once.
15. Restart: start a join, stop the process with Ctrl+C after it has opened Meet, start `run` again. `status` should mention an interrupted meeting, and it must not join that instance again.
16. `meet-notes status` and `meet-notes history` should show the joined, notes, and left times, plus the outcome and the reason. The same row should be in `meeting-log.csv`. If email is enabled, one message should arrive for that attempt. `meet-notes test-email` is the way to check SMTP before a real meeting.
17. Same-minute overlap: put two test events on the calendar with the same start time, each with its own Meet link, and have the second account join both. Both tabs should open during that minute. `history` should show two rows, each joined once, with notes started and a leave time. Neither row should be `missed`. A third event inside the cap of 4 should behave the same way.

Stop the test with `sudo systemctl stop meet-notes` or Ctrl+C. Docker: `docker compose down`.

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium
pytest
```

`pytest` uses mocked Calendar payloads and a local HTML fixture. It does not call Google, does not join a real Meet, and does not send a real email. The default path starts notes without waiting for another person. The fixture also covers a mute that only appears after join, a meeting that is already underway, a host who never admits the account, notes already on, notes unavailable, the overflow menu, ask-to-join, stay until alone, and stay until the scheduled end. The opt-in participant wait is covered by the nobody-joined case. Concurrent tests cover two and three meetings that start in the same minute, a meeting that becomes due while another tab is still open, the cap, and three fixture pages in one persistent profile. Meeting-log and SMTP tests use a fake mail server.

When Meet's UI changes, edit `src/meet_notes/selectors.py` first. Keep the fixture in step with the first selector in each list, then run `pytest`.

## Layout

- `src/meet_notes/selectors.py` — Meet labels and fallbacks.
- `src/meet_notes/meet_flow.py` — join, wait, start notes, verify, leave. One page per call.
- `src/meet_notes/join_pool.py` — which due meetings start together, up to the cap.
- `src/meet_notes/browser.py` — one persistent profile, one page per meeting.
- `src/meet_notes/calendar_sync.py` — window poll, cancellations, time changes. A visit that is already running is not scheduled again.
- `src/meet_notes/state.py` — SQLite history and the one-join claim.
- `Dockerfile`, `docker-compose.yml` — container path.
- `deploy/*.service` — systemd for a venv or for Docker.
- `deploy/com.rev9.meet-notes.plist` — macOS LaunchAgent template. Replace the absolute paths before loading it.
