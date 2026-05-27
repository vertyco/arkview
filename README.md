# ArkView

ArkView is a client-side plugin for the Arkon bot that lets you view your map
data in real time. It runs on your server, parses the save, and serves the data
over HTTP for Arkon to consume.

ArkView processes **one map per instance** — run multiple maps via multiple
instances (one container / one systemd unit / one exe per map).

![Platform](https://img.shields.io/badge/Windows-0078D6?style=for-the-badge&logo=windows&logoColor=white)
![Platform](https://img.shields.io/badge/Linux-FCC624?style=for-the-badge&logo=linux&logoColor=black)
![Python](https://img.shields.io/badge/python-3.10+-orange?style=for-the-badge)
![license](https://img.shields.io/github/license/Vertyco/arkview?style=for-the-badge)

## How it works

Parsing is handled by the pure-Python [`arkparser`](https://pypi.org/project/arkparser/)
library — **no .NET, no external exe**. It reads both engine generations:

- **ASE** (Survival Evolved) — `.ark` flat-binary saves
- **ASA** (Survival Ascended) — `*_WP.ark` SQLite saves

Each parse runs in a short-lived **child process** that writes `ASV_*.json` and
exits, so the multi-GB parse footprint is reclaimed by the OS and the
long-lived server stays lean. There is **no database**: results are written to
an output directory and held in memory until the next parse. ArkView watches
the map file's modification time and re-parses (every ~5s) when the server saves.

> **Migrating from the old (.NET) build?** The HTTP API and response shape are
> unchanged, so the Arkon/AVClient side needs no changes. You no longer need the
> .NET runtime or the bundled `ASVExport.exe`.

## Configuration

ArkView reads a `config.ini` (created on first run; or copy `default_config.ini`).

```ini
[Settings]
# Port for the API to listen on (TCP). Forward it in your router/firewall.
Port = 8000

# Direct path to the .ark map file.
# ASE: .../Saved/SavedArks/TheIsland.ark
# ASA: .../Saved/SavedArks/TheIsland_WP/TheIsland_WP.ark
# Player (.arkprofile) and tribe (.arktribe) files are read from the same directory.
MapFilePath = path/to/your/map.ark

# (Optional) Direct path to the cluster/solecluster folder
ClusterFolderPath = path/to/your/cluster

# (Optional) Direct path to BanList.txt
BanListFile = path/to/your/BanList.txt

# Parser child priority (Windows): LOW, BELOWNORMAL, NORMAL, ABOVENORMAL, HIGH
Priority = LOW

# CPU cores the parser child may use (capped to the host core count)
Threads = 2

# Re-parse when cluster files change (not just the map file)
ReprocessOnArkDataUpdate = False

# If true, the API binds to 127.0.0.1 only
Debug = False

# (Optional) Sentry DSN for error tracking
DSN =

# (Optional) API key for Bearer-token auth
APIKey =
```

Two env vars let one install serve many maps (see systemd below):

- `ARKVIEWER_CONFIG` — path to this instance's `config.ini`
- `ARKVIEWER_OUTPUT` — directory this instance writes `ASV_*.json` to

## Running on Windows (.exe)

1. Download the latest `ArkViewer.exe` from [Releases](https://github.com/vertyco/arkview/releases).
2. Run it anywhere — it creates a `config.ini` next to the exe on first launch.
3. Set `MapFilePath` (and optionally `ClusterFolderPath`), pick a `Port`, and
   forward that port. No .NET required.

## Running from source (Python 3.10+)

```bash
git clone https://github.com/vertyco/arkview.git
cd arkview
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp default_config.ini config.ini   # then edit MapFilePath etc.
python main.py
```

## Running with Docker

One container per map; no database or state volume (output is ephemeral and
re-parsed on restart).

```bash
cp docker-compose.example.yml docker-compose.yml   # edit binds/ports/configs
docker build -t arkviewer:latest .
docker compose up -d
```

Each service binds a per-map `config.ini` at `/app/config.ini` and mounts the
save tree read-only. See `docker-compose.example.yml` for the full layout. If
your save-mount owner isn't UID 1000, build with `--build-arg UID=<uid> GID=<gid>`.

## Running on Ubuntu (systemd)

A templated unit at `deploy/arkview@.service` runs one shared install as many
instances. Assuming code + venv at `/opt/arkviewer` and per-map files under
`/opt/arkviewer/maps/<name>/`:

```bash
sudo cp deploy/arkview@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now arkview@theisland.service
journalctl -u arkview@theisland -f
```

The unit sets `ARKVIEWER_CONFIG`/`ARKVIEWER_OUTPUT` per instance and caps memory
at 4 GB (the parser child peaks ~2 GB on a large map — raise `MemoryMax` for
very large/modded maps).

## Adding to Arkon

Once ArkView is running:

- Type `+viewservers` to open the server menu (cluster + servers already added).
- Select your cluster and click **Servers**.
- Click **ArkView** and enter the port you're running on, then **Submit**
  (IP is usually auto-detected from your server's IP).

## Credits

Parsing is powered by [`arkparser`](https://pypi.org/project/arkparser/). The
exported data schema descends from the ASV lineage
([miragedmuk's ASV](https://github.com/miragedmuk/ASV)) — lots of love for the
groundwork there.

## Contributing

Found a bug or have a suggestion? [Open an issue](https://github.com/vertyco/arkview/issues).
For larger contributions, please reach out on Discord before opening a PR.
