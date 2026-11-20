# Garmin to MyFitnessPal weight sync

Copies your weight from **Garmin Connect** to **MyFitnessPal** once a day, so the weigh-ins from your Garmin scale (or the ones you log in Garmin Connect) also show up in MyFitnessPal.

```bash
./weight_sync.py              # sync the last 7 days
./weight_sync.py --days 30    # a longer window, e.g. the first time
./weight_sync.py --dry-run    # show what would change, without writing anything
```

## How it works

1. **Garmin Connect** is read with the official [Personal API](https://connect.garmin.com/app/settings/personalApiToken), using an API key. For each day it takes the **latest** weigh-in, in kg rounded to one decimal.
2. **MyFitnessPal** has no public API. The script uses the same endpoints as MyFitnessPal's own web app, with the session cookie of a logged-in browser:
   - `GET /api/user-measurements/measurements` reads your weights.
   - `PUT /api/user-measurements/measurements` writes them.
3. For every day of the window with a weigh-in in Garmin Connect:
   - **MyFitnessPal has no weight that day:** the Garmin one is added.
   - **MyFitnessPal has a different weight:** it is replaced by Garmin's.
   - **MyFitnessPal already has the same weight:** nothing is done. Running it twice changes nothing.
4. Weights that **MyFitnessPal sent to Garmin Connect** (when the two accounts are linked) are ignored, so values never go round in circles.

MyFitnessPal renews the session cookie on every visit. The renewed cookie is saved in `.mfp-session.json`, so the connection lasts as long as the script keeps running. If the session expires anyway (for example, after logging out in the browser), the script exits with an error until a new cookie is set.

## Setup

Requires Python 3.10 or newer.

```bash
git clone https://github.com/LuisDiazUgena/garmin-to-my-fitness-pal-weigth-sync.git ~/garmin-mfp-weight-sync
cd ~/garmin-mfp-weight-sync
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env && chmod 600 .env    # then fill it in
./weight_sync.py --dry-run
```

### Credentials (`.env`)

| Setting | What it is |
|---|---|
| `GARMIN_PERSONAL_API_KEY` | Create it at [connect.garmin.com/app/settings/personalApiToken](https://connect.garmin.com/app/settings/personalApiToken). Garmin emails you before it expires. |
| `MFP_SESSION_COOKIE` | Log in at [myfitnesspal.com](https://www.myfitnesspal.com), open the developer tools (F12) → **Network**, reload, click a request to `www.myfitnesspal.com` and copy the value of its `Cookie` request header. The `__Secure-next-auth.session-token` value alone is enough. |
| `SMTP_USER`, `SMTP_PASSWORD`, `NOTIFY_TO` | Optional. When set, an email is sent to `NOTIFY_TO` whenever a sync fails (for example, when the MyFitnessPal session expires). Gmail is used by default, with an [app password](https://myaccount.google.com/apppasswords); for another provider, also set `SMTP_HOST` and `SMTP_PORT` (SSL). |

`.env` and the files where the script keeps the renewed MyFitnessPal session (`.mfp-session.json`) and the Garmin token (`.garmin-token.json`) are in `.gitignore`: they are credentials, so never commit them.

### Every day at 10:00 (systemd)

```bash
cp deploy/systemd/garmin-mfp-weight-sync.* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now garmin-mfp-weight-sync.timer
sudo loginctl enable-linger $USER          # run even when you are not logged in

systemctl --user start garmin-mfp-weight-sync         # sync now
journalctl --user -u garmin-mfp-weight-sync -n 20     # what the last run did
```

The units expect the project at `~/garmin-mfp-weight-sync`. With `Persistent=true`, a run missed while the machine was off happens when it starts.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests
```

## Caveats

- MyFitnessPal's web endpoints are not a public API: they may change without notice. If the sync starts failing with an HTTP error, check the endpoints in your browser's developer tools.
- Units: the weight is written in kilograms; MyFitnessPal shows it in the unit set in your profile.
