# KF8ENC Trunk Console for Linux

This browser console reads SDRTrunk decoded-message and call-event logs. It uses Python's standard library and needs no Flask package or other Python dependencies.

## Install and run

1. Make sure Python 3 is installed. On Ubuntu or Linux Mint, install it with:

   ```bash
   sudo apt update
   sudo apt install python3
   ```

2. Extract the package.
3. Open `config.txt` in a text editor and set `log_dir` to your SDRTrunk event log folder. The default is:

   ```text
   log_dir = ~/SDRTrunk/event_logs
   ```

4. Open a terminal in the extracted folder and run:

   ```bash
   ./launch_console.sh
   ```

   If Linux says the launcher is not executable, run `chmod +x launch_console.sh` once, then launch it again.

5. Open the main dashboard at `http://127.0.0.1:5100`. Choose **Grid View** or open `http://127.0.0.1:5101` directly.

## Ports and grid

The main dashboard listens on port **5100** and binds to all network interfaces, so another device on your local network can open it using the computer's LAN address. The Grid View listens on **127.0.0.1:5101** and is available on the Linux computer running the console.

The Grid View starts with the six talkgroups shown in the supplied layout and adds other talkgroups when SDRTrunk logs activity for them. Green indicates a message logged in the last 12 seconds. This console reads log data and does not provide audio playback.

## Log files and talkgroup names

The app follows files ending in `_decoded_messages.log` and `_call_events.log` in the configured folder. `talkgroups.txt` supplies the bundled Montgomery County talkgroup names; unmapped talkgroups still appear by ID.

`config.txt` is read when the app starts. To stop both views, press **Ctrl+C** in the terminal window.
