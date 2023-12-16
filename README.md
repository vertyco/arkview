# ArkView

A client side API plugin for the Arkon bot to view your map data!

![Platform](https://img.shields.io/badge/Windows-0078D6?style=for-the-badge&logo=windows&logoColor=white)
![Python 3.8](https://img.shields.io/badge/python-v3.11-orange?style=for-the-badge)
![license](https://img.shields.io/github/license/Vertyco/arkview?style=for-the-badge)

![black](https://img.shields.io/badge/style-black-000000?style=for-the-badge&?link=https://github.com/psf/black)
![Lines of code](https://img.shields.io/tokei/lines/github/Vertyco/arkview?color=yellow&label=Lines&style=for-the-badge)
![GitHub repo size](https://img.shields.io/github/repo-size/Vertyco/arkview?color=blueviolet&style=for-the-badge)

# How To Use

### Running on Windows

You will need windows with the latest .NET v6.0 framework to run this client

1. [Get .NET Framework Here](https://dotnet.microsoft.com/en-us/download)
2. Download the latest client from [Releases](https://github.com/vertyco/arkview/releases)
3. Run the .exe anywhere you want, it will make a `config.ini` file that you can set your map and cluster path in
4. Set the port you want the client to listen on and forward it in your router
5. Add that port to the server you have it on with the bot via `[p]avset addport <cluster> <server> <port>`
   1. If you have the client running a different computer from the map, you can use `[p]avset addpi` to add a separate ip for the client

## Running on Linux

Installing dotnet

```bash

wget https://dot.net/v1/dotnet-install.sh

chmod +x dotnet-install.sh

./dotnet-install.sh --channel 6.0 --runtime dotnet
```

```bash
~/.bashrc

# Add the following lines to the end of your ~/.bashrc file:
export DOTNET_ROOT=$HOME/.dotnet
export PATH=$PATH:$HOME/.dotnet

# To apply the changes without restarting the terminal, you can source the .bashrc file:
source ~/.bashrc
```

```bash
chmod +x ArkViewer

./ArkViewer
```

# Credits

This plugin wouldn't be possible without miragedmuk's work on his fork of the old Ark savegame parser, lots of love!
https://github.com/miragedmuk/ASV

# Contributing

If you have any suggestions, or found any bugs, please ping me in Discord (Vertyco#0117)
or [open an issue](https://github.com/vertyco/arkview/issues) on my repo!

If you would like to contribute, please talk to me on Discord first about your ideas before opening a PR.

# Feature Requests

I am open to ideas or suggestions for new cogs and features!

```

```
