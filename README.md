# KF8ENC Trunk Console

A browser dashboard for SDRTrunk event and decoded-message logs. The console uses Python's standard library and does not require Flask or other Python packages.

## Windows setup

1. Install Python 3.10 or newer if it is not already installed. In the installer, enable **Add python.exe to PATH**.
2. Extract this folder somewhere convenient.
3. Open `config.txt` in Notepad and set `log_dir` to the SDRTrunk `event_logs` folder. For example:

   ```text
   log_dir = C:\Users\dougl\SDRTrunk\event_logs
   ```

   Use the actual Windows account name and folder location on your computer. Save the file before starting the console.
4. Double-click `launch_console.bat`. A console window will show the URLs and remain open while the app is running.
5. Browse to `http://127.0.0.1:5100` on the same computer. To use another device on your local network, use the network address printed in the console window.
6. Select **Grid View** on the main dashboard, or open `http://127.0.0.1:5101` directly.

The main dashboard uses port **5100**, so it can run alongside the SDR transcriber on port **5000**. Grid View uses port **5101**. The **Grid View** button redirects through the main dashboard and selects the computer's LAN address when the dashboard was opened at `127.0.0.1`, so the link works from another device as well. If Windows Firewall prompts you, allow access on your private network for both ports **5100** and **5101**.

Its cards show the six talkgroups pictured in the supplied layout and add other talkgroups as activity is logged. **Recent activity only** filters to talkgroups seen in the last 12 seconds. **Known talkgroups** filters to entries with a name or description in `talkgroups.txt`. This console reads SDRTrunk logs, so the grid displays status and activity details; it does not provide audio playback controls.

## Log and talkgroup files

The app follows files ending in `_decoded_messages.log` and `_call_events.log` in the configured folder. The bundled `talkgroups.txt` contains the Montgomery County mapping supplied for this console. Talkgroups without a mapping remain visible by ID.

`config.txt` is read each time the app starts. You can also override its log folder for one launch by setting the `SDRTRUNK_LOG_DIR` environment variable.

## Dashboard

- System ID, NAC, and control frequency from decoded messages and log filenames
- Neighbor sites announced by the monitored control channel, with last-heard times
- Recent talkgroup activity and names from `talkgroups.txt`
- Latest decoded messages and call-event entries

The `RECENT` label means a talkgroup appeared in a decoder log within the last 12 seconds. It indicates recent decoded activity; it does not claim that a call is still active.
