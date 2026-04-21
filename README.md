# 🧟‍♂️ Bitcoin Swarm Intelligence Console (BLSIC) V4 (beta)

Welcome to the BLSIC V4 beta testing environment. This project serves as the central intelligence hub and tactical glass display for Bitcoin ASIC swarms. It features a live terminal dashboard for monitoring hardware telemetry, an integrated gamification engine, and a background FastAPI server that acts as a bridge to extend functionality to the Android Companion App (coming soon).

I have also decided to make the console opensource, and will remain opensource. I will be releasing the companion Android app for remote views, and on the go gamification this will be a low priced app and will only be on android I'm afraid. Potentially if the project gets more support and time allows this may get extended to IOS, but currently no plans to. (Sorry apple users)

There will be a known limitation for the view of the number of miners within the console. As of Apr 19th 2026 I have 7 miners and built the interface with this smaller number in mind.. You can adjust the font and view size within the terminal settings to assist to fit more in. I will be working on a page view to allow for a better interface for tables and view for higher number of miners.  

If you find it useful, feel free to buy me a coffee: bc1qnpn7svcrra6x6dvfcnxuzg3jdc9q08p8lpvzvy

<img width="1287" height="762" alt="image" src="https://github.com/user-attachments/assets/b8cb0687-49ec-41bf-a140-164c752cef61" />

## ✨ Key Features

### 🖥️ Core Console (Python Terminal)
* **Auto-Discovery Engine:** Automatically scans local subnets to find, classify, and connect new ASIC hardware without manual IP entry.
* **The "Luck Virus" Gamification:** A dynamic 72-hour mathematical ladder that tracks hardware "infection" (luck) across the global network, assigning mutation stages based on statistical rarity.
* **Off-Grid Solar Calculator:** Real-time metrics calculating exact solar array and battery requirements based on live Swarm power draw.
* **Companion API:** A headless FastAPI server that broadcasts state arrays and telemetry to the BLSIC network.

### 📱 Android Companion App (Extended Functionality) (COMING SOON)
* **Silicon Maintenance Bay:** *Exclusive to the mobile app.* Features live thermal delta-T tracking against local ambient weather data, warning you of MOSFET/Core degradation before physical hardware failure occurs.
* **Tactical Remote Overrides:** Manage your fleet on the go with direct HTTP reboot signals sent through the API bridge.

INSTALL HELP:

Once you download the project please ensure you create the additional folder structure for the /src/discovery where the hunter.py lives, and then the /calculations folder where the engine.py also needs to be place. 

## 📂 Project Structure
```text
blsic_v4/
├── main_ui.py              # Main entry point for the terminal dashboard
├── data.py                 # Backend logic, data persistence, and FastAPI server
├── requirements.txt        # Python library dependencies
├── src/
│   └── discovery/
│       └── hunter.py       # Network scanner for discovering ASICs
└── calculations/
    └── engine.py           # Mathematical logic for lottery parity and luck scoring
```
*(Note: `swarm_config.json` is intentionally excluded from the repository. The system will automatically generate a fresh configuration file in the root directory on first boot to save local state and settings).*

---

## ⚙️ Prerequisites
* **Python 3.8+** installed on your system.
* A terminal that supports rich text formatting (standard Windows Terminal, Mac Terminal, or Linux bash).

## 🚀 Setup & Installation

To ensure the dependencies do not conflict with your global Python setup, it is highly recommended to run the console inside a Virtual Environment (venv). 

**1. Clone or Download the Repository**
Navigate to your desired directory and clone/extract the project files.
```bash
cd path/to/your/folder
```

**2. Create a Virtual Environment**
```bash
# Windows
python -m venv venv

# Mac/Linux
python3 -m venv venv
```

**3. Activate the Virtual Environment**
```bash
# Windows
venv\Scripts\activate

# Mac/Linux
source venv/bin/activate
```

**4. Install Dependencies**
With the virtual environment active, install the required packages (FastAPI, Uvicorn, Rich, and HTTPX).
```bash
pip install -r requirements.txt
```

---

## 🖥️ Running the Console

Once everything is installed, you can launch the console by running the main UI script. Ensure your virtual environment is activated before running.

```bash
# Windows
python main_ui.py

# Mac/Linux
python3 main_ui.py
```

### 🏁 First Run Experience
Because this repository does not include a `swarm_config.json` file, the console will treat your first launch as a fresh deployment. Here is what to expect:

1. **The Boot Sequence:** The terminal will display the BLSIC OS boot logo and initialize the database.
2. **Auto-Discovery Hunt:** The console will automatically scan your local `192.168.1.x` subnet to hunt for active ASIC miners. If auto hunt doesn't start just press 'H' to kick off the hunter.. 
3. **Data Vault Creation:** Once the scan finishes, it will generate a brand new `swarm_config.json` file in the root folder. This file permanently stores your fleet telemetry, lifetime shares, and custom settings. 
4. **API Bridge:** When you launch `main_ui.py`, the system simultaneously spins up a FastAPI server on `http://0.0.0.0:8000` in the background.

<img width="1230" height="669" alt="image" src="https://github.com/user-attachments/assets/2d7a8ff7-da59-42ac-94d2-b99a53efa609" />

The ASIC device polling may mean you need to run the HUNT a few times if it doesn't pick them up on the first run. This is due to the miners CPU/API being busy when it gets polled to return information so it won't return and answer to say yes I'm here. The discovery engine will pick it up when you run it a few times in my testing to date. Larger swarms will be interesting to see how we can evolve it if it becomes a bottleneck. 
---

## ⌨️ Keyboard Controls

The BLSIC terminal is fully keyboard-driven. Ensure your terminal window is active, and press any of the following hotkeys to control the swarm:

* **`H` (Hunt):** Forces a manual network scan to discover newly connected ASICs.
* **`I` (IP Privacy):** Toggles the hardware matrix to hide the first two octets of your local IP addresses.
* **`S` (Settings):** Opens the prompt to set your local Electricity Cost (£/kWh) and Peak Sun Hours for the solar calculator.
* **`C` (Cost & Tags):** Allows you to input the exact purchase price of each miner to calculate your Swarm Efficiency (£/TH), and manually assign hardware board versions (e.g., v6.1, 5.1). A nifty feature to identify board versions not in the api, and align limits on Watts / Amps etc as a result. "Currently only on NerdQAxe devices for identifying the tagging" cost works for all. 
* **`P` (Power Mode):** Toggles the dashboard into Solar/Off-Grid mode.
* **`A` (Action Terminal):** Opens the remote command center to send targeted HTTP reboot signals to specific rigs.
* **`R` (Reset Views):** Visually recalibrates the trend charts and clears the session peak hashrate. *(Note: This does not delete lifetime data or luck metrics).*
* **`D` (Delete):** Prunes a disconnected or retired miner from your local database.
* **`Q` (Quit):** Safely terminates the network uplinks, saves the neural state to the drive, and shuts down the console.

---

## 🛠️ Supported Hardware & Auto-Detection
The console's `SwarmHunter` engine uses a combination of Hostnames, Device Models, and Board Versions to automatically identify and color-code your hardware on the dashboard. 

**Currently native supported models include:**
* **Gamma** (Board v601) - *Magenta*
* **GT800** (Board v800) - *Orange*
* **NerdQAxe / NerdQAxe++** - *Cyan*
* **Standard Bitaxe** (Board v201 / generic) - *White*

* **NerdMiner / Micro-Scouts** (ESP32 CPU miners) - *Yellow* -- This may not work as in test, and caused some challenges with their api discovery. Might drop anyway as not really worth the hashrate.. ;-) 

**What happens if I connect an unsupported miner?**
If you connect an ASIC that the console does not recognize, the intelligence engine will attempt to look at its hashrate and board version. If it cannot definitively identify the hardware, it will safely default to classifying it as a generic **"Bitaxe"**. >>> is the current logic.. As i have limited Bitaxe HW to date I can add more with community support, plus I have an API discovery tool.

### 🧪 Notes for Testers:
If you encounter a device that is misclassified or showing "Unknown", please note the `deviceModel` and `boardVersion` listed in its local web UI. Looking forward to expanding the detection matrix!

COMING SOON>> testing and sorting out getting published on the playstore. 

## 📱 Android Companion App (Feature Deep Dive)
The BLSIC Android app acts as a tactical glass display, bringing enterprise-grade monitoring and immersive UI feedback directly to your mobile device.

* **Tactical Haptics & Fluidity:** The UI breathes with the live network data. Raw hashrate sensors are wrapped in native `AnimatedContent` for smooth, fluid number ticking, while the underlying `HapticFeedback` API injects subtle physical clicks during navigation and double-buzz alerts when your hardware hits a network anomaly.
* **Silicon Maintenance Bay:** Features live thermal delta-T tracking against local ambient weather data, warning you of MOSFET/Core degradation before physical hardware failure occurs. It actively tracks silicon repaste schedules and calculates hardware thermal penalties.
* **The Horde (Siege Engine):** An alternative gamification dashboard that visualizes your swarm's lifetime accepted shares as an undead horde attacking a "City Wall" (representing the current global network difficulty).
* **Tactical Remote Overrides:** Manage your fleet on the go with direct HTTP reboot signals, pool configuration updates, and target tracking sent directly through the API bridge.
* **Jackpot Dashboard:** Real-time statistical translation of your current hashrate luck into real-world lottery odds (e.g., matching the probability of hitting a block to winning a National Lottery or a £250k Scratchcard).

---

## 🦠 The "Luck Virus" Gamification Engine (Deep Dive)
*Currently supporting BTC and BCH target networks.*

This console does not just track raw hashrate—it maps the mathematical *luck* of your hardware across the global network. A smaller miner can easily outscore a massive rig if it pulls a statistically rarer share. 

### 1. The 72-Hour Luck Ladder & RND DIFF
The swarm operates on a rolling 72-Hour Cycle. Your position on the ladder is driven by the **RND DIFF** (Round Difficulty)—the absolute highest share your miner has found during the current 72H window. 
* **SESS DIFF (Session Diff):** The highest share found since the miner was last booted.
* **The Reboot Risk:** Rebooting a miner instantly kills its current `SESS DIFF` and damages its `REL%` (Reliability Uptime score). However, your `RND DIFF` locks in your high-water mark for the cycle. You must weigh the risk of rebooting a stalled miner to refresh its session against losing uptime points.

### 2. G's, T's, and the Push for a Block
As your hardware hashes, it finds shares of varying difficulty. The console permanently tracks these massive strikes:
* **M (Megadiff):** 100,000,000+ Difficulty
* **G (Gigadiff):** 1,000,000,000+ Difficulty
* **T (Teradiff):** 1,000,000,000,000+ Difficulty
Hitting G's and T's triggers massive point bonuses and visual matrix anomalies on the dashboard, representing severe mathematical proximity to a Block Solve.

### 3. INF (LOG) %
**Infection** is the core metric of the ladder. It is a logarithmic scale measuring the exact distance between your highest `RND DIFF` share and a true Block Solve (100%). Because mining difficulty scales exponentially, the logarithmic percentage accurately visualizes how close your hardware is getting to cracking the network target.

### 4. The Mutation Pathway
As your `INF (LOG)` percentage climbs, the hardware "mutates" through distinct viral stages on the dashboard:
* **IMMUNE (0%):** Standard hashing.
* **SNIFFLE (20%):** Dormant carrier.
* **INFECTIOUS (40%):** Active carrier pulling high-tier shares.
* **VIRULENT (60%):** Extreme network anomaly detected.
* **EPIDEMIC (80%):** Critical viral load. Teradiff strikes occurring.
* **SATOSHI / BCH BLOCK (100%):** Network Compromised. Block solved.
