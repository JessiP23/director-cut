# Director's Cut

**Local-first, agentic video production studio for desktop.**

Director's Cut turns a prompt, brief, or project file into a complete, reviewable, reproducible video production pipeline — running entirely on your machine.

## Architecture

```
┌────────────────────────────────────────────-┐
│  Tauri Desktop Shell (Rust + WebView)       │
│  ┌──────────-┐  ┌───────────────────────┐   │
│  │ Frontend  │  │  Rust Bridge          │   │
│  │ (HTML/JS) │◄─┤  - Process supervisor │   │
│  │           │  │  - IPC commands       │   │
│  └──────────-┘  │  - File system        │   │
│                 └──────────┬────────────┘   │
└────────────────────────────┼────────────────┘
                             │ 
                    HTTP localhost:9420
                             |
┌────────────────────────────┼────────────────┐
│  Python Backend            │                │
│  ┌─────────────-┐  ┌───────┴──────┐         │
│  │ FastAPI      │  │ LangGraph    │         │
│  │ REST + SSE   │  │ Pipeline     │         │
│  └─────────────-┘  └──────────────┘         │
│  ┌─────────┐  ┌──────────┐  ┌──────────┐    │
│  │ SQLite  │  │ FFmpeg   │  │OpenRouter│    │
│  │ (state) │  │ (media)  │  │ (LLMs)   │    │
│  └─────────┘  └──────────┘  └──────────┘    │
└─────────────────────────────────────────────┘
```

## Pipeline Stages

1. **Intake** – Parse user prompt
2. **Planning** – Generate structured production plan
3. **Research** – Gather background info
4. **Script** – Write narration/dialogue *(approval gate)*
5. **Storyboard** – Visual shot list *(approval gate)*
6. **Assets** – Acquire/generate images & clips
7. **Audio** – Generate narration & music
8. **Edit Assembly** – Build timeline *(approval gate)*
9. **QA** – Validate before render *(approval gate)*
10. **Render** – FFmpeg final output
11. **Package** – Multi-format exports
12. **Export** – Write final files

## Quick Start

### Prerequisites
- Rust + Cargo (for Tauri): https://tauri.app/start/prerequisites/
- Python 3.11+
- FFmpeg
- Node.js (optional, for dev tooling)

### Keyboard Shortcuts

| Key | Action |
|---|---|
| ⌘1 | Dashboard |
| ⌘2 | Projects |
| ⌘3 | Runs |
| ⌘4 | Artifacts |
| ⌘5 | Settings |

## Project Structure

```
director-cut/
├── src/                    # Frontend UI (HTML/CSS/JS)
├── src-tauri/              # Rust desktop shell
│   └── src/lib.rs          # Commands: start/stop backend, API proxy
├── backend/                # Python orchestration
│   ├── app/
│   │   ├── agents/         # 12 specialized stage agents
│   │   ├── graph/          # LangGraph pipeline engine
│   │   ├── db/             # SQLite connection + repositories
│   │   ├── routes/         # FastAPI REST endpoints
│   │   ├── schemas/        # Pydantic models
│   │   ├── services/       # LLM gateway, FFmpeg wrapper
│   │   ├── tools/          # LangChain-style tool declarations
│   │   └── runtime/        # Event bus, structured logger
│   ├── tests/
│   └── pyproject.toml
├── data/                   # SQLite DB, cache, exports
├── prompts/                # Agent system prompts
└── README.md
```

## License

MIT


## Commands to run both backend and frontend

- cd backend
- ./venv/bin/python -m uvicorn main:app --reload

### Frontend
- cargo tauri dev

### For other viewports
- cargo tauri ios init

- For Desktop development, run:
  - cargo tauri dev

- For Android development, run:
  - cargo tauri android dev

- For iOS development, run:
  - cargo tauri ios dev