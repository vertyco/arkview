# ArkViewer

ArkViewer is a lightweight FastAPI REST service that parses ARK: Survival Evolved / Ascended save files and exposes game data via HTTP endpoints. It serves as the data backend for the [ArkTools](https://github.com/vertyco/arktools) cog running in [Red-DiscordBot](https://github.com/Cog-Creators/Red-DiscordBot).

ArkViewer processes one map per instance. For multiple maps on a single server, run multiple instances on different ports — natively via systemd, or via Docker Compose.

![Platform](https://img.shields.io/badge/Windows-0078D6?style=for-the-badge&logo=windows&logoColor=white)
![Platform](https://img.shields.io/badge/Linux-FCC624?style=for-the-badge&logo=linux&logoColor=black)
![Platform](https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white)

![Python 3.12+](https://img.shields.io/badge/python-v3.12+-orange?style=for-the-badge)
![license](https://img.shields.io/github/license/Vertyco/arkview?style=for-the-badge)

> ## ⚠️ Support Disclaimer
>
> **The only officially supported way to run ArkViewer is the released `ArkViewer.exe` on Windows.**
>
> Running ArkViewer any other way (from source, on Linux, via Docker, via the systemd template, via docker-compose, on macOS, on a Raspberry Pi, in WSL, anywhere not-Windows-with-the-released-binary) is **unsupported**. The sections below documenting those paths are provided as a courtesy because the code happens to be cross-platform; they are not guaranteed to work, are not tested every release, and **no help, troubleshooting, or bug-fix priority will be given** for issues encountered while running ArkViewer in any unsupported configuration.
>
> If you choose an unsupported path: you're on your own. PRs that improve the unsupported paths are welcome; issues filed against them will be closed.

## v3 Highlights

- **SQLite-backed storage** — parsed game data lives in a per-instance WAL-mode SQLite DB. Cold restarts serve the previous parse immediately while the next reparse runs.
- **Bounded memory** — streaming ingest with atomic staging-table swap. Peak ~1 GB during reparse on TheIsland; steady-state RSS does not grow with request count.
- **Async readers** — routers use `aiosqlite`, so reads never block the event loop while ingest is running.
- **Stability gate** — file watcher waits until ARK finishes flushing a save chunk before parsing, eliminating truncated-read races.
- **Per-scope cooldowns** — `.arkprofile` and cluster writes are rate-limited so high-churn save activity doesn't melt CPU.
- **ASV-legacy response shape** — wire format is bit-for-bit compatible with previous versions; no client refactor needed.
- **Pure Python** — no .NET dependency, no subprocess spawning. Parsing handled by [`arkparser`](https://github.com/vertyco/arkparser).

## Configuration

The first run materialises a `config.ini` next to the binary (or wherever `ARKVIEWER_CONFIG` points).

```ini
[Settings]
# Port for the API to listen on (TCP)
Port = 8000

# Direct path to the .ark map file
# ASE: path to TheIsland.ark
# ASA: path to TheIsland_WP.ark
# Profile (.arkprofile) and tribe (.arktribe) files are discovered
# from the same directory.
MapFilePath =

# (Optional) Direct path to the cluster / solecluster folder
ClusterFolderPath =

# (Optional) Direct path to BanList.txt file
BanListFile =

# Reserved for future use; currently has no effect.
Debug = False

# (Optional) Sentry DSN for error tracking
DSN =

# (Optional) API Key for Bearer token authentication
APIKey =
```

### Environment overrides

| Variable | Default | Purpose |
|---|---|---|
| `ARKVIEWER_CONFIG` | next to the executable | Path to `config.ini`. Set this when running multiple instances from one install. |
| `ARKVIEWER_DB`     | next to `config.ini`     | Path to the SQLite DB. Use when bind-mounting persistent state in containers. |

## Running on Windows (single map)

1. Download the latest `ArkViewer.exe` from [Releases](https://github.com/vertyco/arkview/releases).
2. Run it once — a default `config.ini` is created next to the `.exe`.
3. Edit `config.ini` (`MapFilePath`, `Port`, optional `APIKey`).
4. Forward the port in your router/firewall (TCP).
5. Run `ArkViewer.exe` again — the API is live.

For multiple maps on the same Windows host, copy `ArkViewer.exe` + a unique `config.ini` into a separate folder per map and run each with its own port.

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

# Default config is created on first run; edit before the second run.
python main.py
```

Point one source install at a different config (e.g. for testing another map without copying the repo):

```bash
ARKVIEWER_CONFIG=/path/to/another-map/config.ini python main.py
```

## Running on Ubuntu as a systemd Service (multiple maps)

ArkViewer ships a templated systemd unit so one install can serve N maps from the same host — one process per map.

```bash
sudo systemctl enable --now arkview@theisland.service
sudo systemctl enable --now arkview@ragnarok.service

sudo systemctl restart arkview@theisland
sudo systemctl status  arkview@theisland
journalctl -u arkview@ragnarok -f
```

The `%i` placeholder in the unit (`arkview@<%i>.service`) is substituted with whatever you put after the `@` and used to locate that instance's `config.ini` and DB.

### Layout

```
/opt/arkviewer/                 one shared install
├── .venv/                      Python venv with requirements + arkparser
├── main.py
├── app/
└── maps/                       one subdirectory per map exposed
    ├── theisland/
    │   ├── config.ini          per-map config (its own Port, MapFilePath, …)
    │   └── arkviewer.db        SQLite, written by ArkViewer
    └── ragnarok/
        ├── config.ini
        └── arkviewer.db
```

### Install

```bash
# 1. Clone + venv
sudo mkdir -p /opt/arkviewer && sudo chown $USER:$USER /opt/arkviewer
git clone https://github.com/vertyco/arkview /opt/arkviewer
cd /opt/arkviewer
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. Per-map config dirs (one per map)
mkdir -p maps/theisland maps/ragnarok

# Run once per map to materialise a default config.ini, then edit:
ARKVIEWER_CONFIG=/opt/arkviewer/maps/theisland/config.ini .venv/bin/python main.py  # Ctrl+C once the file is written
ARKVIEWER_CONFIG=/opt/arkviewer/maps/ragnarok/config.ini  .venv/bin/python main.py
# Edit each config.ini: set MapFilePath + a unique Port.

# 3. Dedicated service user
sudo useradd --system --no-create-home --shell /usr/sbin/nologin arkviewer
sudo chown -R arkviewer:arkviewer /opt/arkviewer

# 4. Install the template unit
sudo install -m 0644 deploy/arkview@.service /etc/systemd/system/arkview@.service
sudo systemctl daemon-reload

# 5. Enable + start per-map instances
sudo systemctl enable --now arkview@theisland.service
sudo systemctl enable --now arkview@ragnarok.service
```

To add a third map later: create `maps/<name>/config.ini` with a fresh port, then `sudo systemctl enable --now arkview@<name>.service`. No daemon-reload, no template edit.

The unit (`deploy/arkview@.service`) ships hardened — non-root user, `ProtectSystem=strict`, narrow `ReadWritePaths=`, `MemoryMax=2G` to absorb the parse spike. Adjust paths to match your save tree.

## Running with Docker / Docker Compose (multiple maps)

ArkViewer ships a `Dockerfile` and a `docker-compose.example.yml` for the multi-map case. Copy the example to `docker-compose.yml` (which is gitignored — your host paths and ports are operator-local) and edit it to match your save layout. Each map gets its own container, port, config, and persistent SQLite volume.

### First-time setup

```bash
cp docker-compose.example.yml docker-compose.yml
# edit docker-compose.yml: tweak host ports, container names,
# and the bind-mount paths to your ARK save tree.
```

### Layout (the compose file assumes this)

```
./Dockerfile
./docker-compose.example.yml         tracked - seed template
./docker-compose.yml                 gitignored - your edits
./configs/
├── island.ini
├── rag.ini
└── …                                per-map configs
./state/
├── island/arkviewer.db              persisted across restarts
└── …
/home/pokuser/asa/                   ARK save trees (bind-mounted read-only)
├── Instance_pve-island/…
├── Instance_pve-rag/…
└── Cluster/pvecluster/
```

Inside each per-map `config.ini`, set `MapFilePath` to the path **as seen inside the container** (e.g. `/saves/SavedArks/TheIsland_WP.ark`) — matching the right side of the volume mount.

### Bootstrapping the per-map configs

Docker bind-mounts of single files require the file to exist on the host. Before the first `up`, materialise each config:

```bash
mkdir -p configs state/island state/rag

# Let arkviewer write a default config.ini, then edit it.
ARKVIEWER_CONFIG=$PWD/configs/island.ini python main.py  # Ctrl+C once written
ARKVIEWER_CONFIG=$PWD/configs/rag.ini    python main.py
# Edit each one: set MapFilePath (container path) and keep Port=8000 (the
# port is mapped on the host side via docker-compose.yml).
```

### Single command, all maps

```bash
# Build the image once
docker compose build

# Bring everything up
docker compose up -d

# Tail one service
docker compose logs -f arkviewer-island

# Restart just one map after a config edit
docker compose restart arkviewer-rag

# Tear it all down
docker compose down
```

### Adding another map

Open `docker-compose.yml`, copy one of the service blocks, change the container name, bump the host port, swap the bind-mount paths, and create a matching `configs/<short>.ini`.

### Single-container mode (one map, no compose)

```bash
docker build -t arkviewer:latest .
docker run -d --name arkviewer-island \
  -v $PWD/configs/island.ini:/app/config.ini:ro \
  -v $PWD/state/island:/var/lib/arkviewer \
  -v /home/pokuser/asa/Instance_pve-island/Saved/SavedArks:/saves:ro \
  -p 8101:8000 \
  arkviewer:latest
```

## API Endpoints

All endpoints require `Authorization: Bearer <APIKey>` when `APIKey` is set in config.

### Core

| Method | Path | Description |
|--------|------|-------------|
| GET  | `/`                          | Service metadata (version, map, day, time) |
| GET  | `/stats`                     | Host resource stats (CPU, RAM, disk) |
| GET  | `/data/{dtype}`              | Single dataset: `tamed`, `wild`, `players`, `tribes`, `structures`, `tribelogs`, `mapstructures`, `cluster`, or `all` |
| POST | `/datas`                     | Multiple datasets — body: `{"dtypes": ["tamed", "tribes"]}` |
| GET  | `/data/cluster`              | All cluster files keyed by file_id |
| GET  | `/data/cluster/{file_id}`    | Single cluster file by id |
| GET  | `/tribetames/{gameid}`       | Tamed creatures for a player's tribe (lookup via Steam ID) |
| GET  | `/overlimit/{limit}`         | Tribes exceeding a tame-count limit |
| POST | `/foreigntamescan`           | Find tames from foreign servers — body: `{"servernames": ["ServerName"]}` |
| GET  | `/banlist`                   | Current ban list |
| PUT  | `/updatebanlist`             | Replace ban list — body: `{"banlist": ["steamid1", "steamid2"]}` |

### Filter routes

| Method | Path | Query params |
|--------|------|-------------|
| GET | `/data/filter/tamed`            | `tribe_id`, `class_name`, `is_cryo` |
| GET | `/data/filter/wild`             | `class_name`, `tameable` |
| GET | `/data/filter/players`          | `tribe_id`, `steam_id` |
| GET | `/data/filter/players/{player_id}` | (single record by id) |
| GET | `/data/filter/tribes`           | `tribe_id` |
| GET | `/data/filter/tribes/{tribe_id}`| (single record by id) |
| GET | `/data/filter/structures`       | `tribe_id`, `class_name` |
| GET | `/data/filter/tribelogs`        | `tribe_id` |
| GET | `/data/filter/mapstructures`    | `type` |

### Staleness semantics

- **DB empty** (cold start, ingest not finished): the data routes respond `503 Service Unavailable` with `Retry-After: 30`. Health/banlist routes are unaffected.
- **Last parse > 6 h ago**: the response includes `X-Arkviewer-Stale: true` and `X-Arkviewer-Last-Parse: <ISO timestamp>`. Body is still served — the header is advisory for clients that want to surface staleness.

## Credits

- [arkparser](https://github.com/vertyco/arkparser) — Python ARK save file parser
- Originally based on miragedmuk's [ASV](https://github.com/miragedmuk/ASV) C# exporter

## Contributing

Open an issue or reach out on Discord (Vertyco) before submitting PRs.
