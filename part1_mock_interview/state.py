from dataclasses import dataclass, field
import time


@dataclass
class InterviewState:
    candidate_name: str | None = None
    self_intro_summary: str = ""
    past_experience_notes: list[str] = field(default_factory=list)
    current_stage: str = "intro"          # "intro" | "experience" | "complete"
    stage_start_time: float = field(default_factory=time.time)
