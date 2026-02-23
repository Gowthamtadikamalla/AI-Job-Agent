import asyncio
import logging
import time

from livekit.agents import AgentTask, RunContext, function_tool

from part1_mock_interview.state import InterviewState

logger = logging.getLogger(__name__)

# Maximum time (seconds) allowed for the past-experience stage.
MAX_DURATION = 600  # 10 minutes


class PastExperienceTask(AgentTask):
    """
    Stage 2 of the mock interview: past experience.

    The agent uses the STAR method (Situation, Task, Action, Result) to explore
    2–3 of the candidate's past roles or projects.  It stores structured notes
    for each experience, then calls complete_experience_discussion() to signal
    the TaskGroup that this stage is done.

    A background asyncio timer fires after MAX_DURATION seconds as a safety net.
    """

    def __init__(self) -> None:
        super().__init__(
            instructions="""You are conducting the past-experience portion of a mock job interview.

Your goal is to understand 2–3 specific past experiences using the STAR method:
  Situation  — What was the context?
  Task       — What was the candidate responsible for?
  Action     — What did they specifically do?
  Result     — What was the measurable outcome?

Guidelines:
- Begin with a natural transition that references what you learned in the introduction.
- Ask the candidate to describe a specific project or role they are proud of.
- Use targeted follow-up questions to draw out STAR details.
- Cover 2–3 experiences before wrapping up.
- Record each experience with record_experience() as you discuss it.
- Once 2–3 experiences have been covered in sufficient depth, call complete_experience_discussion()."""
        )
        self._timer_task: asyncio.Task | None = None

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def on_enter(self) -> None:
        """Called by the TaskGroup when this stage becomes active."""
        userdata: InterviewState = self.session.userdata
        userdata.current_stage = "experience"
        userdata.stage_start_time = time.time()

        self._timer_task = asyncio.create_task(self._fallback_timer())

        # Build a natural transition that references the introduction stage.
        candidate_name = userdata.candidate_name or "there"
        await self.session.generate_reply(
            instructions=(
                f"Create a smooth, natural transition into the past-experience stage. "
                f"Reference something specific the candidate mentioned during their "
                f"introduction — for example their role, skills, or career goal. "
                f"Then invite them to walk you through a specific project or role "
                f"they are especially proud of. Address the candidate as '{candidate_name}'."
            )
        )

    async def on_exit(self) -> None:
        """Cancel the timer if the stage completes normally."""
        if self._timer_task and not self._timer_task.done():
            self._timer_task.cancel()

    # ── Fallback timer ───────────────────────────────────────────────────────

    async def _fallback_timer(self) -> None:
        """
        Background safety net: force completion if the stage exceeds MAX_DURATION.
        """
        await asyncio.sleep(MAX_DURATION)

        userdata: InterviewState = self.session.userdata
        if userdata.current_stage != "experience":
            return

        elapsed = time.time() - userdata.stage_start_time
        logger.warning(
            "Past-experience stage timed out after %.0f seconds — forcing transition.",
            elapsed,
        )

        await self.session.generate_reply(
            instructions=(
                "Time for this section is up. Thank the candidate for the experiences "
                "they shared, summarise the key themes in one sentence, and let them "
                "know you will now wrap up the interview."
            ),
            allow_interruptions=False,
        )

        # Guard against race: LLM may have already called complete_experience_discussion()
        # in the tiny window between the stage check and here.
        if self.done():
            return

        result = {
            "experiences": userdata.past_experience_notes,
            "count": len(userdata.past_experience_notes),
            "duration_seconds": elapsed,
            "completed_via": "timeout",
        }
        self.complete(result)

    # ── Tools ────────────────────────────────────────────────────────────────

    @function_tool()
    async def record_experience(
        self,
        context: RunContext,
        company: str,
        role: str,
        key_achievement: str,
    ) -> str:
        """Store a structured note for one past experience after discussing it.

        Args:
            company: Company or organisation name.
            role: The candidate's job title or role.
            key_achievement: A concise description of the main outcome or accomplishment
                using STAR framing (1–2 sentences).
        """
        entry = f"{role} at {company}: {key_achievement}"
        self.session.userdata.past_experience_notes.append(entry)
        count = len(self.session.userdata.past_experience_notes)
        logger.info("Recorded experience %d: %s", count, entry)
        return f"Experience {count} recorded."

    @function_tool()
    async def complete_experience_discussion(self, context: RunContext) -> str:
        """Call this when 2–3 experiences have been discussed in sufficient depth.

        Only call this after you have recorded at least 2 experiences and feel
        the past-experience stage is adequately covered.  This ends the interview
        and triggers the final feedback generation.
        """
        userdata: InterviewState = self.session.userdata
        elapsed = time.time() - userdata.stage_start_time

        if len(userdata.past_experience_notes) < 2:
            return (
                "Please cover at least 2 past experiences before completing this stage. "
                f"So far you have recorded {len(userdata.past_experience_notes)}."
            )

        result = {
            "experiences": userdata.past_experience_notes,
            "count": len(userdata.past_experience_notes),
            "duration_seconds": elapsed,
            "completed_via": "llm",
        }

        logger.info(
            "Past-experience stage completed (%d experiences, %.0f s).",
            len(userdata.past_experience_notes),
            elapsed,
        )
        self.complete(result)
        # Return "" so the LLM has nothing to speak after the tool call.
        # The final feedback is generated by InterviewAgent after the TaskGroup finishes.
        return ""
