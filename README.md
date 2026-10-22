# Oryn - Autonomous Windows Laptop System Agent

```
  ██████╗ ██████╗ ██╗   ██╗███╗   ██╗
 ██╔═══██╗██╔══██╗╚██╗ ██╔╝████╗  ██║
 ██║   ██║██████╔╝ ╚████╔╝ ██╔██╗ ██║
 ██║   ██║██╔══██╗  ╚██╔╝  ██║╚██╗██║
 ╚██████╔╝██║  ██║   ██║   ██║ ╚████║
  ╚═════╝ ╚═╝  ╚═╝   ╚═╝   ╚═╝  ╚═══╝
  system agent v0.2  |  autonomous & loyal
```

**Oryn** is an autonomous personal system agent designed to natively monitor, control, and operate your Windows laptop through modern agentic tool use. Powered by Google Gemini on the Free Tier with zero-overhead native function calling, Oryn executes tasks directly rather than giving manual instructions.

---

## Highlights

- **Native Agentic Architecture**: Uses native typed function calling (Automatic Function Calling via `google-genai`). Eliminates token-wasting legacy `[MSG] + [ACT]` string tags.
- **Optimized for Gemini Free Tier**: Built-in sliding-window rate pacing (max 14 RPM) with inter-request spacing, retry backoff, and automatic failover from `gemini-3.8-flash` to `gemini-3.5-flash-lite` during high-demand spikes.
- **75-Tool OpenAgent Suite**: Integrated tool catalog including Durability Timers, Alarms, Calendar events, Places search (OpenStreetMap Nominatim), Live Sports Scoreboards (ESPN), Google Drive and Notion actions, Three.js 3D Viewer, and Trimble SketchUp.
- **Native Windows Geolocation**: Hooks directly into the Windows Location Service (`Windows.Devices.Geolocation.Geolocator`) using Wi-Fi triangulation rather than imprecise IP blocks.
- **Live System Telemetry & Control**: Inspects CPU, RAM, Disk, top memory-consuming processes, executes PowerShell commands safely, and manages processes.
- **Memory Engine**: Persistent Short-Term Memory (STM) with auto-compression and Long-Term Memory (LTM) for user facts, habits, and preferences.
- **Global Terminal Command**: Runs anywhere in Windows Terminal, PowerShell, or Command Prompt via `setup.bat`.

---

## Quickstart

### 1. One-Click Setup (Windows)

Clone the repository and run the setup script:

```bat
git clone https://github.com/xtreame-x/X-000.git Oryn
cd Oryn
setup.bat
```

The setup script will automatically:
1. Verify Python 3.10+ installation.
2. Install dependencies from `requirements.txt`.
3. Check for your Gemini API key (prompts you to enter your free key from [Google AI Studio](https://aistudio.google.com/)).
4. Register `oryn` in your Windows User PATH (Environment Properties).

### 2. Launch Anywhere

Open any Command Prompt, PowerShell, or Windows Terminal window and type:

```cmd
oryn
```

---

## Google Drive (1-Click Login)

No OAuth Playground, no token copy-pasting. One browser round-trip connects Drive
permanently:

```bat
auth_drive.bat
```

Google opens in your default browser, you click **Allow**, the local page confirms,
and Oryn saves a long-lived `GOOGLE_REFRESH_TOKEN` to `.env` (refreshed automatically
on every use). The same flow is available inside Oryn with `auth drive` or
`login drive`, and it is offered automatically the first time a Drive tool finds no
valid credentials.

Prerequisite (once): create a **Desktop app** OAuth client at
[Google Cloud Console](https://console.cloud.google.com/apis/credentials) with the
Google Drive API enabled, then run `python auth_drive.py menu` and choose option 2 to
store its Client ID and Secret. If you reuse a **Web application** client instead, add
`http://localhost:8085/oauth2callback` to its Authorized redirect URIs, otherwise
Google answers with `redirect_uri_mismatch`.

---

## Notion (1-Click Login)

Notion uses the same browser round-trip as Drive:

```bat
auth_notion.bat
```

Notion opens, you pick a workspace and click **Allow**, and Oryn stores
`NOTION_TOKEN` in `.env`. Inside Oryn the command is `auth notion`, and any Notion
tool that finds no valid token offers the login automatically.

Prerequisite (once): create a **public integration** at
[notion.so/my-integrations](https://www.notion.so/my-integrations), add
`http://localhost:8085/oauth2callback` as a redirect URI, then run
`python auth_notion.py menu` and choose option 2 to store its Client ID and Secret.
Share the pages you want Oryn to use with that integration.

The 10 Notion tools cover search, page and database reads, page and block writes,
queries and database creation (`mcp__Notion__*`, loaded through `tool_search`).

---

## Available Native Tools (16-Tool Agentic Suite)

| Tool | Purpose |
| :--- | :--- |
| `run_command` | Execute PowerShell / CMD commands safely with timeout and output truncation |
| `get_system_status` | Real-time CPU, RAM, Disk, and top memory-consuming processes |
| `file_operation` | Read, write, create, append, delete, move, copy, or search local files |
| `web_search` | Real-time web search for facts, news, and documentation |
| `manage_memory` | Save and recall Short-Term (STM) and Long-Term (LTM) user memories |
| `ui_control` | Browser tab and window navigation controls |
| `create_or_modify_tool` | Dynamically generate and hot-reload new plugins and actions |
| `create_timer` | Start durable countdown timers with end time tracking |
| `create_alarm` | Create durable alarms for specific timestamps |
| `search_calendar` | Query calendar events by title and date range |
| `create_calendar_event`| Create new scheduled events with start/end time and description |
| `search_places` | Physical locations and local places via OpenStreetMap Nominatim |
| `get_sports_scoreboard`| Live scores, fixtures, and standings via ESPN |
| `google_drive_action` | Search, list, read, create, update, or trash Google Drive files |
| `search_openagent_tools`| Search the full 75-tool catalog by keyword or group |
| `openagent_execute` | Universally invoke any tool from the 75-tool catalog dynamically |

---

## Configuration

Settings and provider keys are loaded from `.env` (copy from `.env.example`):

```ini
# Primary Provider (Free Tier recommended)
GEMINI_API_KEY=your_gemini_api_key_here

# Optional Fallback Providers
# GROQ_API_KEY=your_groq_api_key_here
# OPENAI_API_KEY=your_openai_api_key_here
# ANTHROPIC_API_KEY=your_anthropic_api_key_here
```

---

## License

This project is licensed under the [MIT License](LICENSE).
