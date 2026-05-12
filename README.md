# ArkViewer

ArkViewer is a lightweight FastAPI REST service that parses ARK: Survival Evolved / Ascended save files and exposes game data via HTTP endpoints. It serves as the data backend for the ArkTools cog running in [Red-DiscordBot](https://github.com/Cog-Creators/Red-DiscordBot).

ArkViewer processes one map per instance. For multiple maps on a single server, run multiple instances on different ports.

![Platform](https://img.shields.io/badge/Windows-0078D6?style=for-the-badge&logo=windows&logoColor=white)
![Platform](https://img.shields.io/badge/Linux-FCC624?style=for-the-badge&logo=linux&logoColor=black)

![Python 3.12+](https://img.shields.io/badge/python-v3.12+-orange?style=for-the-badge)
![license](https://img.shields.io/github/license/Vertyco/arkview?style=for-the-badge)

## What's New in v3

- **No more .NET dependency** — arkparser (pure Python) replaces the C# ASVExport subprocess
- **Real-time file watching** — watchdog monitors save files and reparses automatically on change
- **Cleaner response shapes** — snake_case keys, nested stat objects, flat response envelopes
- **Faster startup** — no subprocess spawning or JSON file I/O

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

## Running on Windows

1. Download the latest `ArkViewer.exe` from [Releases](https://github.com/vertyco/arkview/releases)
2. Run it — a `config.ini` file is created in the same directory
3. Edit `config.ini` with your map file path and port
4. Forward the port in your router/firewall (TCP)
5. Run `ArkViewer.exe` again to start serving

## Running from Source

```bash
# Clone the repo
git clone https://github.com/vertyco/arkview.git
cd arkview

# Create and activate a virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\Activate.ps1
# Linux:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
pip install -e /path/to/arkparser  # or: pip install arkparser

# Edit config
cp config.ini config.ini  # already exists as default
# Edit config.ini with your paths

# Run
python main.py
```

## Running with Docker

```bash
docker build -t arkviewer .
docker run -p 8000:8000 \
  -v /path/to/config.ini:/app/config.ini \
  -v /path/to/saves:/saves \
  arkviewer
```

Mount your ARK save directory and reference it in `config.ini`.

## API Endpoints

All endpoints require `Authorization: Bearer <APIKey>` header when `APIKey` is set.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Server metadata (version, map, uptime) |
| GET | `/stats` | System resource stats (CPU, RAM, disk, network) |
| GET | `/data/{datatype}` | Single data type: `tamed`, `wild`, `players`, `tribes`, `structures`, `tribelogs`, `mapstructures`, `all` |
| POST | `/datas` | Multiple data types — body: `{"dtypes": ["tamed", "tribes"]}` |
| GET | `/tribetames/{gameid}` | Tamed creatures for a player's tribe (by steam ID) |
| GET | `/overlimit/{limit}` | Tribes exceeding tame count limit |
| POST | `/foreigntamescan` | Find tames from foreign servers — body: `{"servernames": ["ServerName"]}` |
| GET | `/banlist` | Current ban list |
| PUT | `/updatebanlist` | Replace ban list — body: `{"banlist": ["steamid1", "steamid2"]}` |

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

- [arkparser](https://github.com/vertyco/arkparser) — Python ARK save file parser
- Originally based on miragedmuk's [ASV](https://github.com/miragedmuk/ASV) C# exporter

## Contributing

Open an issue or reach out on Discord (Vertyco) before submitting PRs.
