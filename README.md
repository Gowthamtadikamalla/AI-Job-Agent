# Jobnova AI Engineer

A unified platform with three AI-powered modules: **Mock Interview**, **Job Source Agent**, and **Auto-Apply Agent**.

All three modules run from a single **Streamlit dashboard** with an embedded voice client, inline job pipeline, and live auto-apply logs.

---

## Prerequisites

- **Python 3.11+** (tested on 3.13)
- **macOS / Linux** (Windows not tested)

### API Keys

| Service | Sign Up | Used By |
|---|---|---|
| [LiveKit Cloud](https://cloud.livekit.io/) | Free tier available | Part 1 |
| [OpenAI](https://platform.openai.com/) | API key required | Part 1 (LLM), Part 2 (browser agent fallback) |
| [Deepgram](https://deepgram.com/) | Free tier available | Part 1 (speech-to-text) |
| [Cartesia](https://cartesia.ai/) | Free tier available | Part 1 (text-to-speech) |
| [Apify](https://apify.com/) | Free tier available | Part 2 (LinkedIn scraping) |

> **Part 3 requires no API keys** — it uses Playwright + a Chrome Extension locally.

---

## Quick Start

```bash
# 1. Clone and set up environment variables
cp .env.example .env
# Fill in your API keys in .env (see table above)

# 2. Install Streamlit dashboard dependencies
cd streamlit_app
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
deactivate

# 3. Install Part 1 dependencies (mock interview agent)
cd ../part1_mock_interview
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
deactivate

# 4. Install Part 2 dependencies (job sourcing)
cd ../part2_job_source
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
deactivate

# 5. Install Part 3 dependencies (auto-apply)
cd ../part3_auto_apply
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
deactivate

# 6. Launch the dashboard
cd ../streamlit_app
source venv/bin/activate
streamlit run app.py
```

Open `http://localhost:8501` — the home page shows all three modules as clickable cards.

---

## Environment Variables

Copy `.env.example` to `.env` and fill in your keys:

| Variable | Used By | Description |
|---|---|---|
| `LIVEKIT_URL` | Part 1 | LiveKit Cloud WebSocket URL (e.g. `wss://your-project.livekit.cloud`) |
| `LIVEKIT_API_KEY` | Part 1 | LiveKit API key (from Cloud dashboard → Settings) |
| `LIVEKIT_API_SECRET` | Part 1 | LiveKit API secret |
| `OPENAI_API_KEY` | Part 1, 2 | OpenAI API key (GPT-4.1-mini for interview LLM, GPT-4o-mini for browser agent) |
| `DEEPGRAM_API_KEY` | Part 1 | Deepgram API key (Nova-3 speech-to-text) |
| `CARTESIA_API_KEY` | Part 1 | Cartesia API key (Sonic-2 text-to-speech) |
| `CARTESIA_VOICE_ID` | Part 1 | Cartesia voice ID (from Cartesia dashboard → Voices) |
| `APIFY_API_TOKEN` | Part 2 | Apify API token (from Apify Console → Settings → Integrations) |

---

## Part 1 — AI Mock Interview

A LiveKit voice agent that conducts a structured mock interview with two stages and personalised feedback.

**How it works:**
1. **Self-Introduction** (5 min) — Name, role, skills, goals
2. **Past Experience** (10 min) — 2-3 experiences using the STAR method
3. **Feedback** — Strengths, improvements, actionable tips

**From the dashboard:** Click **Start Interview** — the agent launches and an embedded voice widget appears. Click the microphone to connect and start talking.

**Standalone:**
```bash
cd part1_mock_interview && source venv/bin/activate
PYTHONPATH=.. python main.py dev
# Connect via LiveKit Cloud → Sandbox → Web Voice Agent
```

**Tech stack:** LiveKit Agents v1.4.2, Deepgram Nova-3 (STT), Cartesia Sonic-2 (TTS), OpenAI GPT-4.1-mini (LLM), Silero VAD, Multilingual turn detector

---

## Part 2 — AI Job Source Agent

Given a LinkedIn job URL, automatically finds the company's career page and extracts open positions.

**Pipeline:**
1. **LinkedIn scraper** — Extracts company name and domain (Apify + direct HTTP fallback)
2. **Career page discovery** — 4-strategy cascade:
   - ATS pattern matching (Greenhouse, Lever, Workday, SmartRecruiters, Ashby)
   - Direct URL probing (`/careers`, `/jobs`, subdomains)
   - Sitemap XML parsing
   - AI browser agent (last resort)
3. **Job extraction** — ATS APIs, JSON-LD, CSS heuristic scraping

**From the dashboard:** Paste a LinkedIn URL, click **Find Jobs** — results appear inline.

**Standalone:**
```bash
cd part2_job_source && source venv/bin/activate
PYTHONPATH=.. python main.py "https://www.linkedin.com/jobs/view/1234567890"
```

**Output:** `company_name, career_page_url, first_open_position_url`

---

## Part 3 — Resume Auto-Apply Agent

Automatically fills and submits a Lever job application using Playwright + a Chrome Extension. **No API keys required.**

**Before running:**
1. Edit `part3_auto_apply/candidate_data.json` with your candidate information
2. Place your resume as `part3_auto_apply/resume.pdf`

**How it works:**
1. Playwright launches Chrome with the MV3 extension loaded
2. Extension fills all standard Lever fields (React-aware native event dispatch)
3. Handles custom questions, diversity dropdowns, resume upload
4. When Cloudflare Turnstile / hCaptcha is detected — pauses for human
5. After CAPTCHA completion — resumes and submits via CDP trusted mouse event

**From the dashboard:** Fill in candidate info under the **Personal Info** tab, paste a Lever job URL, click **Start Auto-Apply** — live logs stream in the UI.

**Standalone:**
```bash
cd part3_auto_apply && source venv/bin/activate
# Edit candidate_data.json with your info, place resume.pdf in this directory
PYTHONPATH=.. python main.py --show-browser
```

**Target URL:** `https://jobs.lever.co/ekimetrics/d9d64766-3d42-4ba9-94d4-f74cdaf20065`

---

## Project Structure

```
Liba/
├── streamlit_app/
│   ├── app.py                  # Unified dashboard (all 3 parts)
│   └── requirements.txt
├── part1_mock_interview/
│   ├── main.py                 # LiveKit agent entry point
│   ├── state.py                # Interview state dataclass
│   ├── tasks/
│   │   ├── self_intro.py       # Self-introduction stage
│   │   └── past_exp.py         # Past experience stage
│   └── requirements.txt
├── part2_job_source/
│   ├── main.py                 # CLI entry point
│   ├── linkedin_scraper.py     # LinkedIn → company info
│   ├── career_finder.py        # 4-strategy career page discovery
│   ├── job_extractor.py        # ATS API / JSON-LD / CSS extraction
│   └── requirements.txt
├── part3_auto_apply/
│   ├── main.py                 # CLI entry point
│   ├── controller.py           # Playwright orchestrator
│   ├── candidate_data.json     # Candidate info (edit before use)
│   ├── resume.pdf              # Your resume (place here before running)
│   ├── chrome_extension/
│   │   ├── manifest.json       # MV3 extension manifest
│   │   ├── background.js       # Service worker
│   │   └── content.js          # Lever form filler (React-aware)
│   └── requirements.txt
├── .env.example                # Template for API keys
├── .gitignore
└── README.md
```
