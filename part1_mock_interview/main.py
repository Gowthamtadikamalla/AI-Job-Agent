"""
Part 1 — AI Mock Interview
==========================
Entry point for the LiveKit Agents worker.

Architecture:
    InterviewAgent (parent Agent)
        └── TaskGroup (sequential)
              ├── SelfIntroductionTask  (max 5 min + async fallback timer)
              └── PastExperienceTask    (max 10 min + async fallback timer)

Run:
    python main.py dev       # local dev mode (connects to LiveKit playground)
    python main.py start     # production worker mode

Environment variables required (copy .env.example → .env):
    LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET
    OPENAI_API_KEY
    DEEPGRAM_API_KEY
    CARTESIA_API_KEY, CARTESIA_VOICE_ID
"""

import logging
import os

from dotenv import load_dotenv
from livekit.agents import Agent, AgentSession, JobContext, RunContext, WorkerOptions, cli
from livekit.agents.beta.workflows.task_group import TaskGroup
from livekit.plugins import cartesia, deepgram, openai, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from part1_mock_interview.state import InterviewState
from part1_mock_interview.tasks import PastExperienceTask, SelfIntroductionTask

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class InterviewAgent(Agent):
    """
    Parent agent that orchestrates the full mock interview via a TaskGroup.

    After both stages complete (or time out), it generates a final feedback
    summary using the collected interview data.
    """

    def __init__(self) -> None:
        super().__init__(
            instructions="""You are an experienced AI interview coach at Jobnova.
Your role is to run a structured mock job interview and provide constructive feedback.
You are professional, encouraging, and perceptive.
Do not conduct the interview yourself — the TaskGroup handles each stage.
Your job after the stages complete is to synthesise the results into actionable feedback."""
        )

    async def on_enter(self) -> None:
        """
        Launch the sequential TaskGroup and wait for both stages to finish,
        then generate a final feedback summary.
        """
        task_group = TaskGroup()

        task_group.add(
            lambda: SelfIntroductionTask(),
            id="self_intro",
            description="Self-introduction stage",
        )
        task_group.add(
            lambda: PastExperienceTask(),
            id="past_experience",
            description="Past-experience stage",
        )

        logger.info("Starting interview TaskGroup.")
        results = await task_group
        logger.info("TaskGroup finished. Generating feedback.")

        task_results = results.task_results

        intro_result = task_results.get("self_intro", {})
        experience_result = task_results.get("past_experience", {})

        candidate_name = intro_result.get("name") or "the candidate"
        intro_summary = intro_result.get("summary") or "No summary captured."
        experiences = experience_result.get("experiences") or []
        experience_text = "\n".join(f"  • {e}" for e in experiences) if experiences else "  • No experiences recorded."

        await self.session.generate_reply(
            instructions=f"""The mock interview is now complete.  Provide warm, structured feedback to {candidate_name}.

Self-introduction data:
{intro_summary}

Past experiences covered:
{experience_text}

Your feedback should address:
1. Communication clarity and confidence
2. Structure of their self-introduction (was it concise and complete?)
3. Use of the STAR method in their experience descriptions
4. Two specific strengths observed
5. Two specific areas for improvement with actionable tips
6. An encouraging closing statement

Keep the feedback conversational and spoken-word friendly (no bullet lists in speech)."""
        )


async def entrypoint(ctx: JobContext) -> None:
    """
    LiveKit job entrypoint.  Called once per room connection.
    Configures the AgentSession with the full voice pipeline and starts
    the InterviewAgent.
    """
    await ctx.connect()

    session = AgentSession[InterviewState](
        # ── Voice pipeline ────────────────────────────────────────────────
        vad=silero.VAD.load(),
        stt=deepgram.STT(model="nova-3", language="en"),
        llm=openai.LLM(model="gpt-4.1-mini"),
        tts=cartesia.TTS(
            model="sonic-2",
            voice=os.environ["CARTESIA_VOICE_ID"],
        ),
        # ── Turn detection ────────────────────────────────────────────────
        # MultilingualModel (Qwen2.5-0.5B) analyses semantic completion,
        # not just audio pauses — reduces false interruptions by ~39%.
        turn_detection=MultilingualModel(),
        # Longer delays give candidates thinking time during an interview.
        min_endpointing_delay=1.0,   # default 0.5 s — too aggressive for interviews
        max_endpointing_delay=6.0,   # default 3.0 s — too short for complex answers
        # ── Silence / away ────────────────────────────────────────────────
        user_away_timeout=30.0,
        # ── State ─────────────────────────────────────────────────────────
        userdata=InterviewState(),
    )

    await session.start(
        agent=InterviewAgent(),
        room=ctx.room,
    )

    logger.info("Interview session started for room: %s", ctx.room.name)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
