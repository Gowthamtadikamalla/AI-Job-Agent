import asyncio
import logging
import time

from livekit.agents import AgentTask, RunContext, function_tool

from part1_mock_interview.state import InterviewState

logger = logging.getLogger(__name__)

# Maximum time (seconds) allowed for the self-introduction stage.
# If the LLM does not call complete_introduction() within this window,
# the async timer fires and forces a graceful transition.
MAX_DURATION = 300  # 5 minutes


class SelfIntroductionTask(AgentTask):
    """
    Stage 1 of the mock interview: self-introduction.

    The agent asks the candidate to introduce themselves (current/target role,
    years of experience, key skills, career goals).  It stores the candidate's
    name and a brief summary, then calls complete_introduction() to signal the
    TaskGroup to advance to the next stage.

    A background asyncio timer fires after MAX_DURATION seconds to guarantee
    progression even if the LLM never triggers the tool.
    """

    def __init__(self) -> None:
        super().__init__(
            instructions="""You are conducting the self-introduction portion of a mock job interview.

Your goal is to warmly welcome the candidate and ask them to introduce themselves.
Gather the following information naturally through conversation:
- Their name
- Their current role or the role they are targeting
- Years of relevant experience
- Key technical or professional skills
- Career goals

Guidelines:
- Ask ONE focused follow-up question if you need to clarify something important.
- Keep this stage to roughly 3–5 minutes.
- Do NOT ask about specific past projects yet — that comes next.
- When you have enough information, call complete_introduction() to move on.
- Use record_name() as soon as you learn their name.
- Use record_intro_summary() once you have a clear picture of their background."""
        )
        self._timer_task: asyncio.Task | None = None

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def on_enter(self) -> None:
        """Called by the TaskGroup when this stage becomes active."""
        userdata: InterviewState = self.session.userdata
        userdata.current_stage = "intro"
        userdata.stage_start_time = time.time()

        # Launch the fallback timer concurrently — it will not block the session.
        self._timer_task = asyncio.create_task(self._fallback_timer())

        await self.session.generate_reply(
            instructions="Greet the candidate warmly and professionally, then ask them to introduce themselves."
        )

    async def on_exit(self) -> None:
        """Cancel the timer if the stage completes normally."""
        if self._timer_task and not self._timer_task.done():
            self._timer_task.cancel()

    # ── Fallback timer ───────────────────────────────────────────────────────

    async def _fallback_timer(self) -> None:
        """
        Background safety net: if the self-introduction has not completed
        within MAX_DURATION seconds, force a graceful transition.
        """
        await asyncio.sleep(MAX_DURATION)

        userdata: InterviewState = self.session.userdata
        if userdata.current_stage != "intro":
            # Stage already advanced normally — nothing to do.
            return

        elapsed = time.time() - userdata.stage_start_time
        logger.warning(
            "Self-introduction stage timed out after %.0f seconds — forcing transition.",
            elapsed,
        )

        # Ask the agent to wrap up naturally before we complete the task.
        await self.session.generate_reply(
            instructions=(
                "Time for this section is up. In one sentence, acknowledge what the "
                "candidate has shared so far, then say you would like to move on to "
                "discuss their past experience."
            ),
            allow_interruptions=False,
        )

        # Guard against race: LLM may have already called complete_introduction()
        # in the tiny window between the stage check and here.
        if self.done():
            return

        result = {
            "name": userdata.candidate_name,
            "summary": userdata.self_intro_summary,
            "duration_seconds": elapsed,
            "completed_via": "timeout",
        }
        self.complete(result)

    # ── Tools ────────────────────────────────────────────────────────────────

    @function_tool()
    async def record_name(self, context: RunContext, name: str) -> str:
        """Store the candidate's name as soon as you learn it.

        Args:
            name: The candidate's full name.
        """
        self.session.userdata.candidate_name = name
        logger.info("Recorded candidate name: %s", name)
        return f"Name recorded: {name}"

    @function_tool()
    async def record_intro_summary(self, context: RunContext, summary: str) -> str:
        """Store a concise summary of the candidate's self-introduction.

        Args:
            summary: A 2–4 sentence summary covering role, experience, skills, and goals.
        """
        self.session.userdata.self_intro_summary = summary
        logger.info("Recorded intro summary.")
        return "Introduction summary recorded."

    @function_tool()
    async def complete_introduction(self, context: RunContext) -> str:
        """Call this when the self-introduction stage is complete.

        Only call this after you have collected the candidate's name, a summary
        of their background, and feel the introduction is sufficiently covered.
        This will advance the interview to the past-experience stage.
        """
        userdata: InterviewState = self.session.userdata
        elapsed = time.time() - userdata.stage_start_time

        result = {
            "name": userdata.candidate_name,
            "summary": userdata.self_intro_summary,
            "duration_seconds": elapsed,
            "completed_via": "llm",
        }

        logger.info(
            "Self-introduction completed (%.0f s). Advancing to past-experience stage.",
            elapsed,
        )
        self.complete(result)
        # Return "" so the LLM has nothing to speak after the tool call.
        # The natural transition message is handled by PastExperienceTask.on_enter.
        return ""
