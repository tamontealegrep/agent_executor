import sys
import uuid
from pathlib import Path

# Ensure we can import the harness from the same folder
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from compiled_agent_tui import CompiledAgentHarness  # type: ignore


PROFILE_Q_ES = "¿Vas a realizar el proceso como padre soltero, madre soltera o como pareja?"
PROFILE_Q_EN = "Will you perform the process as a single father, a single mother, or as a couple?"
PRIOR_Q_ES = "¿Has realizado tratamientos de fertilidad convencional o subrogación anteriormente"
PRIOR_Q_EN = "Have you performed conventional fertility or surrogacy treatments previously"


def main() -> int:
    slug = "family_aims_sam_text"
    h = CompiledAgentHarness(slug, log_llm=False, log_http=False)
    thread_id = f"test-{uuid.uuid4().hex[:8]}"

    def asks_profile(msgs: list[str]) -> bool:
        blob = "\n".join(msgs)
        return (PROFILE_Q_ES in blob) or (PROFILE_Q_EN in blob)

    def asks_prior(msgs: list[str]) -> bool:
        blob = "\n".join(msgs).lower()
        return ("conventional fertility" in blob) or ("tratamientos de fertilidad" in blob)

    # Start outbound style (agent greets first)
    r = h.start(thread_id, first_speaker="agent")

    # Drive a generic path until the profile question appears
    steps = 0
    max_steps = 15
    while steps < max_steps and not asks_profile(r.messages):
        # If we reached the prior-treatments question, we've already advanced past profile
        if asks_prior(r.messages):
            print("PASS: Reached prior-treatments question — profile was already resolved (no loop).")
            return 0
        steps += 1
        # Heuristic replies to common earlier questions
        blob = "\n".join(r.messages).lower()
        if "ivf" in blob or "ropa" in blob or "gestational surrogacy" in blob or "subrogac" in blob or "surrog" in blob:
            r = h.turn(thread_id, "subrogación")
            continue
        if "nacionalidad" in blob or "nationality" in blob:
            r = h.turn(thread_id, "colombiano")
            continue
        if "primera vez" in blob or "first time" in blob:
            r = h.turn(thread_id, "si")
            continue
        if "conocimiento" in blob or "do you know" in blob:
            r = h.turn(thread_id, "no")
            continue
        if "interested in me telling you more" in blob or "¿estás interesado en que te cuente" in blob or "interesado en que te cuente" in blob:
            r = h.turn(thread_id, "yes")
            continue
        # Default noop to advance if none matched (avoid stalling)
        r = h.turn(thread_id, "ok")

    if not asks_profile(r.messages):
        print("FAIL: Did not reach the family-profile question within steps limit.")
        for m in r.messages:
            print(f"Bot: {m}")
        return 1

    # Answer profile and ensure we advance
    r = h.turn(thread_id, "padre soltero")
    state = (r.current_state or "")
    advanced = ("CL_ASK_PRIOR" in state) or asks_prior(r.messages)
    not_reasked = (PROFILE_Q_ES not in "\n".join(r.messages)) and (PROFILE_Q_EN not in "\n".join(r.messages))

    if advanced and not_reasked:
        print("PASS: Profile answer treated as continuation; advanced without re-asking.")
        return 0

    # One more nudge
    r = h.turn(thread_id, "padre soltero")
    state = (r.current_state or "")
    advanced2 = ("CL_ASK_PRIOR" in state) or asks_prior(r.messages)
    not_reasked2 = (PROFILE_Q_ES not in "\n".join(r.messages)) and (PROFILE_Q_EN not in "\n".join(r.messages))
    if advanced2 and not_reasked2:
        print("PASS: Profile answer treated as continuation after single retry; advanced without re-asking.")
        return 0

    print(f"FAIL: Still not advancing. current_state='{state}'.")
    for m in r.messages:
        print(f"Bot: {m}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
