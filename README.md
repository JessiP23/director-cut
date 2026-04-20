# Director's Cut# Director's Cut



**AI-powered video production studio for desktop.****Local-first, agentic video production studio for desktop.**



Director's Cut turns a text prompt into a complete video — scripting, storyboarding, AI video generation, and final render — all automated.Director's Cut turns a prompt, brief, or project file into a complete, reviewable, reproducible video production pipeline — running entirely on your machine.



## Features## Architecture



- 🎬 **12-stage pipeline** — intake, planning, research, script, storyboard, assets, audio, edit, QA, render, package, export```

- 🤖 **AI video generation** via fal.ai (Wan 2.2, LTX, MiniMax, Kling)┌────────────────────────────────────────────-┐

- 📝 **LLM-powered scripting** via Groq (Llama 3.3 70B)│  Tauri Desktop Shell (Rust + WebView)       │

- 🎞️ **FFmpeg rendering** — normalize, concat, export│  ┌──────────-┐  ┌───────────────────────┐   │

- 💾 **Local-first** — SQLite database, all data on your machine│  │ Frontend  │  │  Rust Bridge          │   │

- 🖥️ **Desktop app** — Tauri v2 (Rust + WebView)│  │ (HTML/JS) │◄─┤  - Process supervisor │   │

│  │           │  │  - IPC commands       │   │

## Quick Start│  └──────────-┘  │  - File system        │   │

│                 └──────────┬────────────┘   │

### Prerequisites└────────────────────────────┼────────────────┘

                             │ 

| Tool | Required | Install |                    HTTP localhost:9420

|------|----------|---------|                             |

| **Rust + Cargo** | ✅ | [tauri.app/start/prerequisites](https://tauri.app/start/prerequisites/) |┌────────────────────────────┼────────────────┐

| **Python 3.9+** | ✅ | `brew install python@3.11` |│  Python Backend            │                │

| **FFmpeg** | ✅ | `brew install ffmpeg` |│  ┌─────────────-┐  ┌───────┴──────┐         │

| **Groq API Key** | ✅ | Free at [console.groq.com](https://console.groq.com) |│  │ FastAPI      │  │ LangGraph    │         │

| **fal.ai API Key** | ✅ | [fal.ai/dashboard](https://fal.ai/dashboard) |│  │ REST + SSE   │  │ Pipeline     │         │

│  └─────────────-┘  └──────────────┘         │

### 1. Clone & Setup│  ┌─────────┐  ┌──────────┐  ┌──────────┐    │

│  │ SQLite  │  │ FFmpeg   │  │OpenRouter│    │

```bash│  │ (state) │  │ (media)  │  │ (LLMs)   │    │

git clone https://github.com/JessiP23/director-cut.git│  └─────────┘  └──────────┘  └──────────┘    │

cd director-cut└─────────────────────────────────────────────┘

``````



### 2. Backend Setup## Pipeline Stages



```bash1. **Intake** – Parse user prompt

cd backend2. **Planning** – Generate structured production plan

python3 -m venv venv3. **Research** – Gather background info

source venv/bin/activate4. **Script** – Write narration/dialogue *(approval gate)*

pip install -r requirements.txt   # or: pip install fastapi uvicorn httpx aiosqlite groq5. **Storyboard** – Visual shot list *(approval gate)*

cd ..6. **Assets** – Acquire/generate images & clips

```7. **Audio** – Generate narration & music

8. **Edit Assembly** – Build timeline *(approval gate)*

### 3. Run in Dev Mode9. **QA** – Validate before render *(approval gate)*

10. **Render** – FFmpeg final output

```bash11. **Package** – Multi-format exports

cargo tauri dev12. **Export** – Write final files

```

## Quick Start

The app will open automatically. On first launch you'll see the **onboarding screen** — enter your Groq and fal.ai API keys to get started.

### Prerequisites

### 4. Create a Production- Rust + Cargo (for Tauri): https://tauri.app/start/prerequisites/

- Python 3.11+

1. Select or create a **Project** from the dropdown- FFmpeg

2. Type your video idea (e.g. "A 30-second explainer about climate change")- Node.js (optional, for dev tooling)

3. Set max scenes (1–8) — fewer scenes = cheaper & faster

4. Click **Produce** → watch the pipeline run### Keyboard Shortcuts



## Cost Guide| Key | Action |

|---|---|

| Model | Cost per clip | 2 scenes | 4 scenes || ⌘1 | Dashboard |

|-------|--------------|----------|----------|| ⌘2 | Projects |

| **Wan 2.2** (default) | ~$0.04 | ~$0.08 | ~$0.16 || ⌘3 | Runs |

| **LTX Video** | ~$0.01 | ~$0.02 | ~$0.04 || ⌘4 | Artifacts |

| MiniMax Hailuo | ~$0.45 | ~$0.90 | ~$1.80 || ⌘5 | Settings |

| Kling 2.5 Turbo | ~$0.32 | ~$0.64 | ~$1.28 |

## Project Structure

Change the model in **Settings → Video Model**.

```

## Build for Distributiondirector-cut/

├── src/                    # Frontend UI (HTML/CSS/JS)

### macOS (.dmg)├── src-tauri/              # Rust desktop shell

│   └── src/lib.rs          # Commands: start/stop backend, API proxy

```bash├── backend/                # Python orchestration

cargo tauri build│   ├── app/

```│   │   ├── agents/         # 12 specialized stage agents

│   │   ├── graph/          # LangGraph pipeline engine

Output: `src-tauri/target/release/bundle/dmg/Director's Cut_0.1.0_aarch64.dmg`│   │   ├── db/             # SQLite connection + repositories

│   │   ├── routes/         # FastAPI REST endpoints

**Important for testers:** The `backend/` folder must be placed next to the `.app` bundle, or included in the distribution zip. Testers need:│   │   ├── schemas/        # Pydantic models

1. The `.app` or `.dmg`│   │   ├── services/       # LLM gateway, FFmpeg wrapper

2. The `backend/` folder (with `venv/`)│   │   ├── tools/          # LangChain-style tool declarations

3. FFmpeg installed (`brew install ffmpeg`)│   │   └── runtime/        # Event bus, structured logger

│   ├── tests/

### Distribution Package│   └── pyproject.toml

├── data/                   # SQLite DB, cache, exports

```bash├── prompts/                # Agent system prompts

# Create a distributable zip└── README.md

cargo tauri build```

mkdir -p dist

cp -r src-tauri/target/release/bundle/macos/director-cut.app dist/## License

cp -r backend dist/backend

cd dist && zip -r director-cut-macos.zip director-cut.app backendMIT

```



Share the `director-cut-macos.zip` — testers unzip and run the app. Onboarding will prompt for API keys.## Commands to run both backend and frontend



## Keyboard Shortcuts- cd backend

- ./venv/bin/python -m uvicorn main:app --reload

| Key | Action |

|-----|--------|### Frontend

| ⌘1 | Command Center |- cargo tauri dev

| ⌘2 | Projects |

| ⌘3 | Productions |### For other viewports

| ⌘4 | Media Library |- cargo tauri ios init

| ⌘5 | Settings |

- For Desktop development, run:

## Project Structure  - cargo tauri dev



```- For Android development, run:

director-cut/  - cargo tauri android dev

├── src/                    # Frontend UI (HTML/CSS/JS)

├── src-tauri/              # Rust desktop shell- For iOS development, run:

│   └── src/lib.rs          # Process supervisor + API proxy  - cargo tauri ios dev
├── backend/                # Python orchestration
│   ├── app/
│   │   ├── agents/         # 12 specialized stage agents
│   │   ├── graph/          # Pipeline engine
│   │   ├── db/             # SQLite repositories
│   │   ├── routes/         # FastAPI REST endpoints
│   │   ├── services/       # LLM gateway
│   │   └── runtime/        # Event bus
│   └── .env                # API keys (auto-managed by Settings)
└── data/                   # SQLite DB + exports
```

## License

MIT



## Releases

- ./scripts/release.sh           # patch bump: 0.2.0 → 0.2.1
- ./scripts/release.sh minor     # minor bump: 0.2.0 → 0.3.0
- ./scripts/release.sh 0.5.0     # exact version