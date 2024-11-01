# ArkView

ArkView is a client side plugin for the Arkon bot that allows you to view your map data in real time. It's a simple client that runs on your server and listens for requests from Arkon. It then sends the data back to Arkon for you to view.

ArkView only processes one server per instance, so if you are running multiple maps on a single server you will need multiple instances of ArkView running.

![Platform](https://img.shields.io/badge/Windows-0078D6?style=for-the-badge&logo=windows&logoColor=white)
![Platform](https://img.shields.io/badge/Linux-FCC624?style=for-the-badge&logo=linux&logoColor=black)

![Python 3.8](https://img.shields.io/badge/python-v3.11-orange?style=for-the-badge)
![license](https://img.shields.io/github/license/Vertyco/arkview?style=for-the-badge)

![black](https://img.shields.io/badge/style-black-000000?style=for-the-badge&?link=https://github.com/psf/black)
![GitHub repo size](https://img.shields.io/github/repo-size/Vertyco/arkview?color=blueviolet&style=for-the-badge)

# Configuration

The client uses a `config.ini` file to store the configuration. The file is created when you run the client for the first time. You can also create it manually by copying the `default_config.ini` file from the repo and renaming it to `config.ini`.

```ini
[Settings]
# Port for the API to listen on (TCP)
# Make sure to forward this port in your router and allow it as TCP in your firewall
Port = 8000

# Direct path to the .ark map file
MapFilePath = path/to/your/map.ark

# (Optional): Direct path to the solecluster folder
ClusterFolderPath = path/to/your/solecluster

# (Optional): Direct path to BanList.txt file
BanListFile = path/to/your/BanList.txt

# Process priority(Windows-only): LOW, BELOWNORMAL, NORMAL, ABOVENORMAL, HIGH
Priority = LOW

# Number of threads to use for processing (if higher than CPU threads, it will be set to CPU threads)
Threads = 2

# If true, api will only be accessible locally (If running as python, this will cause the client to fail)
Debug = False

# (Optional): Set a sentry DSN for error tracking
DSN =

# (Optional): API Key for authentication
APIKey =
```

# Running on Windows

You will need windows with the latest .NET v6.0 framework to run this client

1. [Get .NET Framework Here](https://dotnet.microsoft.com/en-us/download)
2. Download the latest client from [Releases](https://github.com/vertyco/arkview/releases)
3. Run the .exe anywhere you want, it will make a `config.ini` file that you can set your map and cluster path in
4. Set the port you want the client to listen on and forward it in your router

# Running on Linux (ASE ONLY) [UNSUPPORTED!]

This assumes you have a basic understanding of Linux and how to use the terminal.
Support for running on Linux is experimental and may not work as expected. As such I cannot provide support for this method.

Run the following commands to install the required dependencies

```bash
# Update existing packages
sudo apt update && sudo apt upgrade -y

# Install the .NET 6.0 SDK and runtime
sudo apt -y install dotnet-sdk-6.0
sudo apt -y install aspnetcore-runtime-6.0

# Install python and essential dependencies
sudo apt -y install python3.11 python3.11-dev python3.11-venv git build-essential nano

# Create a virtual environment
python3.11 -m venv ~/arkenv

# Activate the virtual environment
source ~/arkenv/bin/activate

# Clone the repository and cd into it
git clone https://github.com/vertyco/arkview.git
cd arkview

# Install the required python packages
pip install -r requirements.txt

# Edit the config.ini file to your liking
cp default_config.ini config.ini
sudo nano config.ini  # Save and exit with ctrl + O; enter; ctrl + X

# Run the client
python3.11 main.py
```

## Setting up Auto-Start on Boot for linux

First, your Linux `username` can be fetched with the following command:

```bash
whoami
```

Next, your python path can be fetched with the following commands:

```bash
source ~/arkenv/bin/activate
/usr/bin/which python
```

Then create the new service file:

`sudo nano /etc/systemd/system/arkview.service`

Paste the following in the file, and replace all instances of `username` with the Linux username you retrieved above, and `path` with the python path you retrieved above.

```ini
[Unit]
Description=arkview
After=multi-user.target
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=path -O -m main.py --no-prompt
User=username
Group=username
Type=idle
Restart=on-abnormal
RestartSec=15
RestartForceExitStatus=1
RestartForceExitStatus=26
TimeoutStopSec=10

[Install]
WantedBy=multi-user.target
```

Save and exit `ctrl + O; enter; ctrl + x`

### Starting and enabling the service

```bash
# Starting the service
sudo systemctl start arkview

# Enabling the service
sudo systemctl enable arkview

# Stopping the service
sudo systemctl stop arkview
```

# Adding to Arkon

After you have the client running, you can add it to Arkon by doing the following:

- Type `+viewservers` to open the server menu, this assumes you've already added your cluster and servers to Arkon.
- Select the cluster you want and click the `Servers` button.
- Click the `ArkView` button and a modal will pop up.
- Enter the port of the client you're running, and click `Submit` (IP is usually not needed as it uses your server's IP)

## EXTRA: Samba (For running on windows but syncing with linux)

```bash
sudo apt update
sudo apt install samba
```

`sudo nano /etc/samba/smb.conf`

```conf
# Scroll to the bottom of the file and add a new share definition. For example:
[ShareName]
path = /path/to/your/directory
browseable = yes
writable = yes
guest ok = yes
create mask = 0777
directory mask = 0777
```

# Credits

This plugin wouldn't be possible without miragedmuk's work on his fork of the old Ark savegame parser, lots of love!
https://github.com/miragedmuk/ASV

# Contributing

If you have any suggestions, or found any bugs, please ping me in Discord (Vertyco#0117)
or [open an issue](https://github.com/vertyco/arkview/issues) on my repo!

If you would like to contribute, please talk to me on Discord first about your ideas before opening a PR.
