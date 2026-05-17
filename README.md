# ArkViewer

ArkViewer is a lightweight FastAPI REST service that parses ARK: Survival Evolved / Ascended save files and exposes game data via HTTP endpoints. It serves as the data backend for the ArkTools cog running in [Red-DiscordBot](https://github.com/Cog-Creators/Red-DiscordBot).

ArkViewer processes one map per instance. For multiple maps on a single server, run multiple instances on different ports.

![Platform](https://img.shields.io/badge/Windows-0078D6?style=for-the-badge&logo=windows&logoColor=white)
![Platform](https://img.shields.io/badge/Linux-FCC624?style=for-the-badge&logo=linux&logoColor=black)

![Python 3.12+](https://img.shields.io/badge/python-v3.12+-orange?style=for-the-badge)
![license](https://img.shields.io/github/license/Vertyco/arkview?style=for-the-badge)

> ## ⚠️ Support Disclaimer
>
> **The only officially supported way to run ArkViewer is the released `ArkViewer.exe` on Windows.**
>
> Running ArkViewer any other way (from source, on Linux, via Docker, via the systemd template, via docker-compose, on macOS, on a Raspberry Pi, in WSL, anywhere not-Windows-with-the-released-binary) is **unsupported**. The sections below documenting those paths are provided as a courtesy because the code happens to be cross-platform; they are not guaranteed to work, are not tested every release, and **no help, troubleshooting, or bug-fix priority will be given** for issues encountered while running ArkViewer in any unsupported configuration.
>
> If you choose an unsupported path: you're on your own. PRs that improve the unsupported paths are welcome; issues filed against them will be closed.

## What's New in v3

- **No more .NET dependency** - arkparser (pure Python) replaces the C# ASVExport subprocess
- **Real-time file watching** - watchdog monitors save files and reparses automatically on change
- **Cleaner response shapes** - snake_case keys, nested stat objects, flat response envelopes
- **Faster startup** - no subprocess spawning or JSON file I/O

## Configuration

The client uses a `config.ini` file created automatically on first run.

```ini
[Settings]
# Port for the API to listen on (TCP)
Port = 8000

# Direct path to the .ark map file
# ASE: path to TheIsland.ark
# ASA: path to TheIsland_WP.ark
# Profiles and tribe files are discovered from the same directory
MapFilePath =

# (Optional) Direct path to the cluster/solecluster folder
ClusterFolderPath =

# (Optional) Direct path to BanList.txt file
BanListFile =

# If true, API binds to 127.0.0.1 only
Debug = False

# (Optional) Sentry DSN for error tracking
DSN =

# (Optional) API Key for Bearer token authentication
APIKey =
```

## Running on Windows (single map)

1. Download the latest `ArkViewer.exe` from [Releases](https://github.com/vertyco/arkview/releases).
2. Run it once - a default `config.ini` is created next to the .exe.
3. Edit `config.ini` (`MapFilePath`, `Port`, optional `APIKey`).
4. Forward the port in your router/firewall (TCP).
5. Run `ArkViewer.exe` again - the API is now live.

For multiple maps on the same Windows host, copy `ArkViewer.exe` + a unique `config.ini` into a separate folder per map and run each one with its own port.

## Running from Source

```bash
git clone https://github.com/vertyco/arkview.git
cd arkview

python -m venv .venv
# Windows:
.venv\Scripts\Activate.ps1
# Linux:
source .venv/bin/activate

pip install -r requirements.txt

# A default config.ini is created on first run; edit it before second run.
python main.py
```

To point one source install at a different config file (e.g. for testing another map without copying the repo), set `ARKVIEWER_CONFIG`:

```bash
ARKVIEWER_CONFIG=/path/to/another-map/config.ini python main.py
```

## Running on Ubuntu as a systemd Service (multiple maps)

ArkViewer ships a systemd **template** unit so one install can serve N maps from the same host, one process per map:

```
sudo systemctl start  arkviewer@ragnarok.service
sudo systemctl start  arkviewer@theisland.service
sudo systemctl restart arkviewer@ragnarok
sudo systemctl status arkviewer@theisland
journalctl -u arkviewer@ragnarok -f
```

The `%i` placeholder in the unit (`arkviewer@<%i>`) is replaced with whatever you put after the `@` and used to locate that instance's config.ini.

### Layout

```
/opt/arkviewer/                  one shared install - code lives here
├── .venv/                       python venv with requirements + arkparser
├── main.py
├── app/
└── maps/                        one subdirectory per map you want exposed
    ├── ragnarok/
    │   └── config.ini           Ragnarok's config (its own Port, MapFilePath, ...)
    └── theisland/
        └── config.ini           TheIsland's config
```

### Install

```bash
# 1. Clone + venv
sudo mkdir -p /opt/arkviewer && sudo chown $USER:$USER /opt/arkviewer
git clone https://github.com/vertyco/arkview /opt/arkviewer
cd /opt/arkviewer
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. Make a per-map config directory
mkdir -p maps/ragnarok maps/theisland
# Run once per map to materialize a default config.ini, then edit:
ARKVIEWER_CONFIG=/opt/arkviewer/maps/ragnarok/config.ini  .venv/bin/python main.py    # Ctrl+C after it creates the file
ARKVIEWER_CONFIG=/opt/arkviewer/maps/theisland/config.ini .venv/bin/python main.py    # same
# Edit each maps/<name>/config.ini with that map's MapFilePath + a unique Port.

# 3. Service user
sudo useradd --system --no-create-home --shell /usr/sbin/nologin arkviewer
sudo chown -R arkviewer:arkviewer /opt/arkviewer

# 4. Install the template unit
sudo install -m 0644 deploy/arkviewer@.service /etc/systemd/system/arkviewer@.service
sudo systemctl daemon-reload

# 5. Enable + start per-map instances
sudo systemctl enable --now arkviewer@ragnarok.service
sudo systemctl enable --now arkviewer@theisland.service
```

To add a third map later: create `maps/<name>/config.ini` with a fresh port, then `sudo systemctl enable --now arkviewer@<name>.service`. No daemon-reload, no template edits.

The unit file (`deploy/arkviewer@.service`) ships with reasonable hardening defaults - non-root user, read-only filesystem access to `/srv/ark`, memory limits sized for parse spikes. Adjust `ReadOnlyPaths=` to wherever your ARK servers actually write their saves.

## Running with Docker / Docker Compose (multiple maps)

ArkViewer ships a `Dockerfile` and a `docker-compose.yml` for the multi-map case. Each map gets its own container with its own port and its own `config.ini`.

### Layout (the compose file assumes this)

```
./Dockerfile
./docker-compose.yml
./deploy/configs/
├── ragnarok/config.ini
└── theisland/config.ini
/srv/ark/                        outside the repo - your ARK save trees
├── ragnarok/saves/Ragnarok.ark
└── theisland/saves/TheIsland.ark
```

Inside each `config.ini`, set `MapFilePath` to the path **as seen inside the container** - same as the right side of the volume mount in `docker-compose.yml`.

### Bootstrapping the per-map configs

Docker bind-mounts of single files require the file to exist on the host. Before the first `up`, create each map's `config.ini` from the default:

```bash
mkdir -p deploy/configs/ragnarok deploy/configs/theisland

# Quickest way: let arkviewer materialize the default config for you, then edit.
ARKVIEWER_CONFIG=$PWD/deploy/configs/ragnarok/config.ini  python main.py   # Ctrl+C after the file appears
ARKVIEWER_CONFIG=$PWD/deploy/configs/theisland/config.ini python main.py
# Edit each one - set MapFilePath (container path) and a unique Port (always 8000 inside the container).
```

### Single command, all maps

```bash
# Build the image once
docker compose build

# Bring everything up
docker compose up -d

# Tail one service
docker compose logs -f arkviewer-ragnarok

# Restart just one map after a config edit
docker compose restart arkviewer-theisland

# Tear it all down
docker compose down
```

### Adding another map

Open `docker-compose.yml`, copy one of the existing service blocks, change the container name, bump the host port, swap the volume paths. A commented-out example for an ASA Scorched Earth is at the bottom of the file as a template.

### Single-container "I just want one map" mode

If you only need one map and don't want compose:

```bash
docker build -t arkviewer:latest .
docker run -d --name arkviewer-ragnarok \
  -v /srv/ark/ragnarok/config.ini:/app/config.ini:ro \
  -v /srv/ark/ragnarok/saves:/srv/ark/ragnarok/saves:ro \
  -p 8000:8000 \
  arkviewer:latest
```

## API Endpoints

All endpoints require `Authorization: Bearer <APIKey>` header when `APIKey` is set.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Server metadata (version, map, uptime) |
| GET | `/stats` | System resource stats (CPU, RAM, disk, network) |
| GET | `/data/{datatype}` | Single data type: `tamed`, `wild`, `players`, `tribes`, `structures`, `tribelogs`, `mapstructures`, `all` |
| POST | `/datas` | Multiple data types - body: `{"dtypes": ["tamed", "tribes"]}` |
| GET | `/tribetames/{gameid}` | Tamed creatures for a player's tribe (by steam ID) |
| GET | `/overlimit/{limit}` | Tribes exceeding tame count limit |
| POST | `/foreigntamescan` | Find tames from foreign servers - body: `{"servernames": ["ServerName"]}` |
| GET | `/banlist` | Current ban list |
| PUT | `/updatebanlist` | Replace ban list - body: `{"banlist": ["steamid1", "steamid2"]}` |

### Filtered Routes (bonus)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/data/filter/tamed` | Filter by `tribe_id`, `class_name`, `is_cryo` |
| GET | `/data/filter/wild` | Filter by `class_name`, `tameable` |
| GET | `/data/filter/players` | Filter by `tribe_id`, `steam_id` |
| GET | `/data/filter/players/{player_id}` | Single player by ID |
| GET | `/data/filter/tribes` | Filter by `tribe_id` |
| GET | `/data/filter/tribes/{tribe_id}` | Single tribe by ID |
| GET | `/data/filter/structures` | Filter by `tribe_id`, `class_name` |
| GET | `/data/filter/tribelogs` | Filter by `tribe_id`, `day` |
| GET | `/data/filter/mapstructures` | Filter by `type` |

## Credits

- [arkparser](https://github.com/vertyco/arkparser) - Python ARK save file parser
- Originally based on miragedmuk's [ASV](https://github.com/miragedmuk/ASV) C# exporter

## Contributing

Open an issue or reach out on Discord (Vertyco) before submitting PRs.
