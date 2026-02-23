"""
Jobnova AI Engineer — Unified Streamlit Dashboard
"""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
import streamlit as st
from dotenv import load_dotenv

# ── Project root setup ────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Jobnova AI Engineer",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .block-container { padding-top: 1.5rem; max-width: 1100px; }

    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #0f0f23 0%, #1a1a3e 100%);
    }
    [data-testid="stSidebar"] * { color: #e0e0e0 !important; }

    .main-header {
        background: linear-gradient(90deg, #6366f1, #8b5cf6, #a78bfa);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        font-size: 2.5rem;
        font-weight: 800;
        margin-bottom: 0.1rem;
    }
    .sub-header {
        color: #94a3b8;
        font-size: 1.05rem;
        margin-bottom: 1.5rem;
    }

    .log-box {
        background: #0f172a;
        color: #e2e8f0;
        border-radius: 0.5rem;
        padding: 1rem;
        font-family: monospace;
        font-size: 0.85rem;
        max-height: 400px;
        overflow-y: auto;
        white-space: pre-wrap;
    }
</style>
""", unsafe_allow_html=True)


# ── Page names (used for sidebar and navigation) ─────────────────────────────
PAGE_HOME = "🏠 Home"
PAGE_INTERVIEW = "🎙️ Mock Interview"
PAGE_SOURCE = "🔍 Job Source"
PAGE_APPLY = "📝 Auto-Apply"

PAGES = [PAGE_HOME, PAGE_INTERVIEW, PAGE_SOURCE, PAGE_APPLY]

# ── Apply queued navigation (set by home-page buttons BEFORE the widget renders) ─
if "_nav_to" in st.session_state:
    st.session_state["nav_radio"] = st.session_state.pop("_nav_to")

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🚀 Jobnova")
    st.markdown("---")

    page = st.radio(
        "Navigate",
        PAGES,
        label_visibility="collapsed",
        key="nav_radio",
    )

    st.markdown("---")
    st.caption("Jobnova AI Engineer Challenge")


# ══════════════════════════════════════════════════════════════════════════════
# HOME
# ══════════════════════════════════════════════════════════════════════════════
if page == PAGE_HOME:
    st.markdown('<p class="main-header">Jobnova AI Engineer</p>', unsafe_allow_html=True)
    st.markdown('<p class="sub-header">AI-powered job interview prep, job sourcing, and auto-apply — all in one place.</p>', unsafe_allow_html=True)

    col1, col2, col3 = st.columns(3, gap="large")

    with col1:
        with st.container(border=True):
            st.markdown("### 🎙️ Mock Interview")
            st.markdown("AI voice agent that runs a structured mock interview with real-time feedback.")
            st.markdown("**Self-intro → Past experience → Feedback**")
            if st.button("Open Mock Interview →", key="home_btn_interview", use_container_width=True):
                st.session_state["_nav_to"] = PAGE_INTERVIEW
                st.rerun()

    with col2:
        with st.container(border=True):
            st.markdown("### 🔍 Job Source")
            st.markdown("Paste a LinkedIn job URL and instantly find the company's career page and open positions.")
            st.markdown("**LinkedIn → Career page → Job URLs**")
            if st.button("Open Job Source →", key="home_btn_source", use_container_width=True):
                st.session_state["_nav_to"] = PAGE_SOURCE
                st.rerun()

    with col3:
        with st.container(border=True):
            st.markdown("### 📝 Auto-Apply")
            st.markdown("Automatically fill and submit Lever job applications with your candidate data.")
            st.markdown("**Fill form → Upload resume → Submit**")
            if st.button("Open Auto-Apply →", key="home_btn_apply", use_container_width=True):
                st.session_state["_nav_to"] = PAGE_APPLY
                st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# PART 1: MOCK INTERVIEW
# ══════════════════════════════════════════════════════════════════════════════
elif page == PAGE_INTERVIEW:
    st.markdown('<p class="main-header">AI Mock Interview</p>', unsafe_allow_html=True)
    st.markdown('<p class="sub-header">Voice-powered mock interview with real-time AI feedback</p>', unsafe_allow_html=True)

    # ── Env check ──────────────────────────────────────────────────────────
    required_keys = [
        "LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET",
        "OPENAI_API_KEY", "DEEPGRAM_API_KEY", "CARTESIA_API_KEY", "CARTESIA_VOICE_ID",
    ]
    missing = [k for k in required_keys if not os.environ.get(k)]

    if missing:
        st.error(f"Missing environment variables: **{', '.join(missing)}**. Add them to your `.env` file.")

    # ── Interview stages ───────────────────────────────────────────────────
    st.markdown("The interview has **two stages**, followed by personalised feedback:")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown("**1. Self-Introduction** (5 min)")
        st.markdown("Name, role, skills, goals")
    with col2:
        st.markdown("**2. Past Experience** (10 min)")
        st.markdown("2-3 experiences using STAR method")
    with col3:
        st.markdown("**3. Feedback**")
        st.markdown("Strengths, improvements, tips")

    st.markdown("---")

    # ── State init ────────────────────────────────────────────────────────
    if "p1_process" not in st.session_state:
        st.session_state["p1_process"] = None
    if "p1_running" not in st.session_state:
        st.session_state["p1_running"] = False
    if "p1_logs" not in st.session_state:
        st.session_state["p1_logs"] = []
    if "p1_token" not in st.session_state:
        st.session_state["p1_token"] = None

    # ── Token generation helper ──────────────────────────────────────────
    def _generate_livekit_token() -> str:
        """Generate a LiveKit access token for the interview room."""
        from livekit.api import AccessToken, VideoGrants
        import time as _time

        lk_key = os.environ.get("LIVEKIT_API_KEY", "")
        lk_secret = os.environ.get("LIVEKIT_API_SECRET", "")
        room_name = f"interview-{int(_time.time())}"

        token = AccessToken(lk_key, lk_secret) \
            .with_identity("candidate") \
            .with_name("Candidate") \
            .with_grants(VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
            ))
        return token.to_jwt()

    # ── Launch controls ──────────────────────────────────────────────────
    col_start, col_stop, _ = st.columns([1, 1, 3])

    with col_start:
        start_clicked = st.button(
            "Start Interview",
            type="primary",
            disabled=bool(missing) or st.session_state["p1_running"],
        )
    with col_stop:
        stop_clicked = st.button(
            "Stop Interview",
            disabled=not st.session_state["p1_running"],
        )

    if start_clicked and not st.session_state["p1_running"]:
        # Kill any stale agent processes from previous sessions
        try:
            subprocess.run(["pkill", "-f", "part1_mock_interview/main.py"], capture_output=True)
        except Exception:
            pass

        import tempfile
        log_file = tempfile.NamedTemporaryFile(
            mode="w", suffix="_lk_agent.log", delete=False, prefix="jobnova_"
        )
        st.session_state["p1_log_path"] = log_file.name

        # Start the agent subprocess, redirect all output to a log file
        venv_python = str(PROJECT_ROOT / "part1_mock_interview" / "venv" / "bin" / "python")
        main_py = str(PROJECT_ROOT / "part1_mock_interview" / "main.py")
        env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT), "PYTHONUNBUFFERED": "1"}
        proc = subprocess.Popen(
            [venv_python, main_py, "dev"],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=str(PROJECT_ROOT / "part1_mock_interview"),
        )
        log_file.close()  # subprocess writes to the file; we read it separately
        st.session_state["p1_process"] = proc
        st.session_state["p1_running"] = True
        st.session_state["p1_logs"] = []
        # Generate token for the embedded client
        st.session_state["p1_token"] = _generate_livekit_token()
        st.rerun()

    if stop_clicked and st.session_state["p1_process"]:
        st.session_state["p1_process"].terminate()
        try:
            subprocess.run(["pkill", "-f", "part1_mock_interview/main.py"], capture_output=True)
        except Exception:
            pass
        # Clean up log file
        log_path = st.session_state.get("p1_log_path")
        if log_path and os.path.exists(log_path):
            try:
                os.unlink(log_path)
            except Exception:
                pass
        st.session_state["p1_process"] = None
        st.session_state["p1_running"] = False
        st.session_state["p1_token"] = None
        st.session_state["p1_log_path"] = None
        st.rerun()

    # ── Running state ────────────────────────────────────────────────────
    if st.session_state["p1_running"]:
        proc = st.session_state["p1_process"]
        log_path = st.session_state.get("p1_log_path")

        # Read log output from the log file
        if log_path and os.path.exists(log_path):
            try:
                with open(log_path, "r") as lf:
                    lines = lf.read().strip().split("\n")
                    st.session_state["p1_logs"] = [ln for ln in lines if ln][-200:]
            except Exception:
                pass

        # Detect if agent process has exited
        if proc and proc.poll() is not None:
            st.session_state["p1_running"] = False
            st.session_state["p1_process"] = None
            st.error("Agent has stopped unexpectedly.")
            if st.session_state["p1_logs"]:
                st.code("\n".join(st.session_state["p1_logs"][-20:]), language="text")
        else:
            # Agent is running — show embedded voice widget
            logs_text = "\n".join(st.session_state["p1_logs"])
            agent_ready = "registered worker" in logs_text

            if not agent_ready:
                st.info("Agent is starting up... please wait.")
                import time
                time.sleep(2)
                st.rerun()
            else:
                st.success("Agent is ready! Click the microphone button below to start your interview.")

                livekit_url = os.environ.get("LIVEKIT_URL", "")
                token = st.session_state.get("p1_token", "")

                # Embedded LiveKit voice client
                import streamlit.components.v1 as components

                livekit_html = f"""
                <div id="lk-app" style="
                    background: #0f172a; border-radius: 12px; padding: 24px;
                    text-align: center; font-family: -apple-system, sans-serif;
                    color: #e2e8f0; min-height: 300px;
                ">
                    <div id="status" style="font-size: 1.1rem; margin-bottom: 16px; color: #94a3b8;">
                        Click the microphone to connect...
                    </div>

                    <div id="visualizer" style="
                        height: 80px; display: flex; align-items: center;
                        justify-content: center; gap: 4px; margin: 20px 0;
                    "></div>

                    <button id="mic-btn" onclick="toggleConnection()" style="
                        width: 72px; height: 72px; border-radius: 50%;
                        background: #6366f1; border: none; cursor: pointer;
                        display: flex; align-items: center; justify-content: center;
                        margin: 0 auto; transition: all 0.2s;
                    ">
                        <svg id="mic-icon" width="32" height="32" viewBox="0 0 24 24"
                             fill="none" stroke="white" stroke-width="2">
                            <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
                            <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
                            <line x1="12" y1="19" x2="12" y2="23"/>
                            <line x1="8" y1="23" x2="16" y2="23"/>
                        </svg>
                    </button>

                    <div id="transcript" style="
                        margin-top: 20px; padding: 12px; background: #1e293b;
                        border-radius: 8px; min-height: 40px; text-align: left;
                        font-size: 0.9rem; color: #cbd5e1; max-height: 200px;
                        overflow-y: auto;
                    ">
                        <em>Transcript will appear here...</em>
                    </div>
                </div>

                <script src="https://cdn.jsdelivr.net/npm/livekit-client@2.9.1/dist/livekit-client.umd.js"></script>
                <script>
                    const LIVEKIT_URL = "{livekit_url}";
                    const TOKEN = "{token}";
                    let room = null;
                    let connected = false;

                    const statusEl = document.getElementById('status');
                    const micBtn = document.getElementById('mic-btn');
                    const transcriptEl = document.getElementById('transcript');
                    const visualizerEl = document.getElementById('visualizer');

                    // Create visualizer bars
                    for (let i = 0; i < 20; i++) {{
                        const bar = document.createElement('div');
                        bar.style.cssText = 'width:4px;background:#6366f1;border-radius:2px;height:4px;transition:height 0.1s;';
                        bar.className = 'viz-bar';
                        visualizerEl.appendChild(bar);
                    }}

                    async function toggleConnection() {{
                        if (connected) {{
                            await disconnect();
                        }} else {{
                            await connect();
                        }}
                    }}

                    async function connect() {{
                        try {{
                            statusEl.textContent = 'Connecting...';
                            room = new LivekitClient.Room({{
                                audioCaptureDefaults: {{ echoCancellation: true, noiseSuppression: true }},
                            }});

                            room.on(LivekitClient.RoomEvent.TrackSubscribed, (track) => {{
                                if (track.kind === 'audio') {{
                                    const el = track.attach();
                                    document.body.appendChild(el);
                                }}
                            }});

                            room.on(LivekitClient.RoomEvent.DataReceived, (payload, participant) => {{
                                try {{
                                    const msg = JSON.parse(new TextDecoder().decode(payload));
                                    if (msg.text) {{
                                        const who = participant ? participant.identity : 'agent';
                                        transcriptEl.innerHTML += '<div><strong>' + who + ':</strong> ' + msg.text + '</div>';
                                        transcriptEl.scrollTop = transcriptEl.scrollHeight;
                                    }}
                                }} catch(e) {{}}
                            }});

                            room.on(LivekitClient.RoomEvent.Disconnected, () => {{
                                connected = false;
                                statusEl.textContent = 'Disconnected. Click mic to reconnect.';
                                micBtn.style.background = '#6366f1';
                                stopVisualizer();
                            }});

                            await room.connect(LIVEKIT_URL, TOKEN);
                            await room.localParticipant.setMicrophoneEnabled(true);

                            connected = true;
                            statusEl.textContent = 'Connected — speak to start your interview!';
                            micBtn.style.background = '#ef4444';
                            transcriptEl.innerHTML = '<em>Interview in progress...</em>';
                            startVisualizer();

                        }} catch(err) {{
                            statusEl.textContent = 'Error: ' + err.message;
                            console.error(err);
                        }}
                    }}

                    async function disconnect() {{
                        if (room) {{
                            await room.disconnect();
                            room = null;
                        }}
                        connected = false;
                        statusEl.textContent = 'Disconnected. Click mic to reconnect.';
                        micBtn.style.background = '#6366f1';
                        stopVisualizer();
                    }}

                    let vizInterval = null;
                    function startVisualizer() {{
                        const bars = document.querySelectorAll('.viz-bar');
                        vizInterval = setInterval(() => {{
                            bars.forEach(bar => {{
                                const h = connected ? Math.random() * 60 + 4 : 4;
                                bar.style.height = h + 'px';
                            }});
                        }}, 150);
                    }}
                    function stopVisualizer() {{
                        clearInterval(vizInterval);
                        document.querySelectorAll('.viz-bar').forEach(b => b.style.height = '4px');
                    }}
                </script>
                """

                components.html(livekit_html, height=480)

                # Show logs in collapsed expander
                if st.session_state["p1_logs"]:
                    with st.expander("Agent Logs", expanded=False):
                        st.code("\n".join(st.session_state["p1_logs"][-30:]), language="text")
    else:
        st.caption("Click **Start Interview** to launch the AI interviewer and connect directly.")


# ══════════════════════════════════════════════════════════════════════════════
# PART 2: JOB SOURCE
# ══════════════════════════════════════════════════════════════════════════════
elif page == PAGE_SOURCE:
    st.markdown('<p class="main-header">AI Job Source</p>', unsafe_allow_html=True)
    st.markdown('<p class="sub-header">Find any company\'s career page and open positions from a LinkedIn job URL</p>', unsafe_allow_html=True)

    linkedin_url = st.text_input(
        "LinkedIn Job URL",
        placeholder="https://www.linkedin.com/jobs/view/1234567890",
    )

    run_btn = st.button("Find Jobs", type="primary", disabled=not linkedin_url)

    if run_btn and linkedin_url:
        st.markdown("---")

        # ── Step 1 ─────────────────────────────────────────────────────────
        with st.status("Step 1/3 — Scraping LinkedIn...", expanded=True) as s1:
            try:
                from part2_job_source.linkedin_scraper import LinkedInScraper

                scraper = LinkedInScraper()
                company_info = scraper.scrape_job_page(linkedin_url)

                company_name = company_info.get("company_name", "")
                company_domain = company_info.get("company_domain", "")
                job_title = company_info.get("job_title", "")

                c1, c2 = st.columns(2)
                c1.metric("Company", company_name or "—")
                c2.metric("Domain", company_domain or "—")
                if job_title:
                    st.caption(f"Job title: {job_title}")

                if not company_domain:
                    s1.update(label="Step 1/3 — Could not determine domain", state="error")
                    st.error("Could not determine the company's domain from LinkedIn.")
                    st.stop()

                s1.update(label="Step 1/3 — LinkedIn scraped", state="complete")
            except Exception as e:
                s1.update(label="Step 1/3 — Failed", state="error")
                st.error(str(e))
                st.stop()

        # ── Step 2 ─────────────────────────────────────────────────────────
        career_url = None
        with st.status("Step 2/3 — Finding career page...", expanded=True) as s2:
            try:
                from part2_job_source.career_finder import find_career_page

                career_result = asyncio.run(find_career_page(company_domain))
                career_url = career_result.get("career_url")
                strategy = career_result.get("strategy", "—")
                confidence = career_result.get("confidence", 0)

                if career_url:
                    st.metric("Career Page", career_url)
                    st.caption(f"Found via **{strategy}** (confidence {confidence:.0%})")
                    s2.update(label="Step 2/3 — Career page found", state="complete")
                else:
                    s2.update(label="Step 2/3 — Not found", state="error")
                    st.warning("Could not find a career page for this company.")
                    st.stop()
            except Exception as e:
                s2.update(label="Step 2/3 — Failed", state="error")
                st.error(str(e))
                st.stop()

        # ── Step 3 ─────────────────────────────────────────────────────────
        positions = []
        if career_url:
            with st.status("Step 3/3 — Extracting positions...", expanded=True) as s3:
                try:
                    from part2_job_source.job_extractor import get_open_positions

                    positions = asyncio.run(get_open_positions(career_url))

                    if positions:
                        st.metric("Positions Found", len(positions))
                        s3.update(label=f"Step 3/3 — {len(positions)} position(s) found", state="complete")
                    else:
                        s3.update(label="Step 3/3 — No positions found", state="error")
                        st.warning("No open positions found on the career page.")
                except Exception as e:
                    s3.update(label="Step 3/3 — Failed", state="error")
                    st.error(str(e))

        # ── Results ────────────────────────────────────────────────────────
        if positions:
            st.markdown("---")
            st.markdown("### Results")

            first_pos = positions[0]
            st.success(f"**{company_name}** — Career page: [{career_url}]({career_url})")

            table_data = []
            for p in positions[:20]:
                table_data.append({
                    "Title": p.get("title", "—"),
                    "Location": p.get("location", "—"),
                    "URL": p.get("url", ""),
                })
            st.dataframe(table_data, use_container_width=True, hide_index=True)

            st.markdown("**Output:**")
            st.code(
                f"{company_name}, {career_url}, {first_pos.get('url', 'N/A')}",
                language="text",
            )


# ══════════════════════════════════════════════════════════════════════════════
# PART 3: AUTO-APPLY
# ══════════════════════════════════════════════════════════════════════════════
elif page == PAGE_APPLY:
    st.markdown('<p class="main-header">Auto-Apply Agent</p>', unsafe_allow_html=True)
    st.markdown('<p class="sub-header">Automatically fill and submit Lever job applications</p>', unsafe_allow_html=True)

    # ── Load candidate data ────────────────────────────────────────────────
    data_path = PROJECT_ROOT / "part3_auto_apply" / "candidate_data.json"
    if data_path.exists():
        with open(data_path) as f:
            candidate_data = json.load(f)
    else:
        candidate_data = {
            "identity": {"name": "", "email": "", "phone": "", "location": "", "company": ""},
            "files": {"resume_path": "resume.pdf"},
            "custom_answers": {},
        }

    identity = candidate_data.get("identity", {})
    files_data = candidate_data.get("files", {})
    custom = candidate_data.get("custom_answers", {})

    # ── Candidate form ─────────────────────────────────────────────────────
    st.markdown("### Candidate Information")

    tab_id, tab_resume, tab_answers = st.tabs(["Personal Info", "Resume", "Custom Answers"])

    with tab_id:
        c1, c2 = st.columns(2)
        identity["name"] = c1.text_input("Full Name", value=identity.get("name", ""))
        identity["email"] = c2.text_input("Email", value=identity.get("email", ""))
        identity["phone"] = c1.text_input("Phone", value=identity.get("phone", ""))
        identity["location"] = c2.text_input("Location", value=identity.get("location", ""))
        identity["company"] = c1.text_input("Company", value=identity.get("company", ""))

    with tab_resume:
        resume_path = st.text_input(
            "Resume file path",
            value=files_data.get("resume_path", "resume.pdf"),
            help="Relative to part3_auto_apply/ or absolute path",
        )
        files_data["resume_path"] = resume_path
        full_resume = PROJECT_ROOT / "part3_auto_apply" / resume_path if not os.path.isabs(resume_path) else Path(resume_path)
        if full_resume.exists():
            st.success(f"Found: {full_resume.name}")
        else:
            st.warning(f"Not found: {full_resume}")

    with tab_answers:
        custom_json = st.text_area(
            "Custom answers (JSON)",
            value=json.dumps(custom, indent=2),
            height=250,
            help="Keys = question keywords, Values = your answers",
        )
        try:
            custom = json.loads(custom_json)
        except json.JSONDecodeError:
            st.error("Invalid JSON — fix before running.")

    # Auto-save
    updated = {"identity": identity, "files": files_data, "custom_answers": custom}
    if updated != candidate_data:
        with open(data_path, "w") as f:
            json.dump(updated, f, indent=2)

    st.markdown("---")

    # ── Job URL & run ──────────────────────────────────────────────────────
    st.markdown("### Apply to Job")

    job_url = st.text_input(
        "Lever Job URL",
        value="https://jobs.lever.co/ekimetrics/d9d64766-3d42-4ba9-94d4-f74cdaf20065",
    )

    show_browser = st.checkbox("Show browser window", value=True, help="Required for CAPTCHA solving")

    launch = st.button("Start Auto-Apply", type="primary")

    if launch:
        with open(data_path, "w") as f:
            json.dump(updated, f, indent=2)

        st.markdown("---")

        browser_flag = "--show-browser" if show_browser else ""
        cmd = (
            f"cd {PROJECT_ROOT / 'part3_auto_apply'} && "
            f"source venv/bin/activate && "
            f"PYTHONPATH={PROJECT_ROOT} python main.py "
            f'--job-url "{job_url}" '
            f"--data {data_path} "
            f"{browser_flag}"
        )

        with st.status("Running auto-apply agent...", expanded=True) as agent_status:
            log_area = st.empty()

            try:
                proc = subprocess.Popen(
                    ["bash", "-c", cmd],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )

                output_lines = []
                for line in iter(proc.stdout.readline, ""):
                    stripped = line.rstrip()
                    if stripped:
                        output_lines.append(stripped)
                        display = "\n".join(output_lines[-30:])
                        log_area.markdown(f'<div class="log-box">{display}</div>', unsafe_allow_html=True)

                proc.wait()

                full_output = "\n".join(output_lines)

                if proc.returncode == 0 or "submitted successfully" in full_output.lower():
                    agent_status.update(label="Application submitted!", state="complete")
                    st.success("Application submitted successfully!")
                elif "captcha" in full_output.lower() or "human" in full_output.lower():
                    agent_status.update(label="CAPTCHA detected — check browser", state="running")
                    st.warning("CAPTCHA detected. Complete it in the browser window, then the agent will continue.")
                else:
                    agent_status.update(label="Agent finished", state="complete")
                    st.info("Agent finished. Check the browser window for results.")

            except Exception as e:
                agent_status.update(label="Error", state="error")
                st.error(f"Failed: {e}")
