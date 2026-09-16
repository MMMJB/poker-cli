"""The review must never reveal a persona, and never an unshown card."""

from __future__ import annotations

import dataclasses
import json
import random
import re

import pytest

from poker.agents.personas import ALL_PERSONAS, assign_seats, leak_denylist
from poker.app.review import ReviewCoach, _scrub, render_review_context
from poker.app.session import HandRecord
from poker.config import CFG
from poker.engine.actions import Action
from poker.engine.cards import card_str
from poker.engine.hand import HandEngine
from poker.engine.state import GameConfig

NAMES = ["You", "Deb", "Walter", "Tank", "Raj", "Sofia"]
DENY = tuple(w.lower() for w in leak_denylist())


def make_record(seed: int) -> tuple[HandRecord, HandEngine]:
    e = HandEngine(GameConfig(), NAMES, [300] * 6, button=seed % 6,
                   hand_id=seed, seed=seed)
    e.start()
    stacks_before = {p.seat: p.stack + p.total_committed for p in e.state.players}
    decisions = []
    while not e.is_complete():
        seat = e.current_actor()
        legal = e.legal_actions()
        action = Action.check() if legal.can_check else Action.call()
        decisions.append({"seat": seat, "street": e.state.street.name.lower(),
                          "action": str(action), "source": "model"})
        e.apply(action)
    result = e.result()
    personas = assign_seats(random.Random(seed), 0)
    return HandRecord(
        hand_id=result.hand_id,
        button=result.button,
        hero_seat=0,
        names={i: NAMES[i] for i in range(6)},
        events=[ev.to_dict(include_private=True) for ev in e.log.all()],
        public_events=[ev.to_dict() for ev in e.log.public()],
        decisions=decisions,
        personas={s: p.key for s, p in personas.items()},
        result=result,
        stacks_before=stacks_before,
    ), e


# --------------------------------------------------------------------------
# Structural: the coach's context simply does not contain the secret
# --------------------------------------------------------------------------

def test_context_contains_no_archetype_or_profile() -> None:
    """Table names are public; archetypes and profiles are the secret."""
    for seed in range(40):
        record, _ = make_record(seed)
        text = render_review_context(record).lower()
        for persona in ALL_PERSONAS:
            pattern = re.compile(rf"\b{re.escape(persona.archetype)}\b")
            assert not pattern.search(text)
            assert persona.profile.lower() not in text
            for word in persona.leak_words:
                assert not re.search(rf"\b{re.escape(word)}\b", text)


def test_the_denylist_permits_naming_a_player() -> None:
    """The prompt invites 'Walter in the big blind' -- the scrubber must allow it.

    Every nameplate an archetype can wear has to pass, not just the first.
    """
    for persona in ALL_PERSONAS:
        for name in persona.names:
            line = f"{name} in the big blind called every street."
            assert _scrub(line, DENY) == line, f"{name} was wrongly scrubbed"


def test_context_contains_no_unshown_hole_cards() -> None:
    """The largest leak surface is closed by never putting the data in."""
    token = re.compile(r"\b([2-9TJQKA][shdc])\b")
    for seed in range(60):
        record, e = make_record(seed)
        text = render_review_context(record)
        named = set(token.findall(text))

        allowed = {card_str(c) for c in record.result.board}
        allowed |= {card_str(c) for c in record.result.hole_cards[record.hero_seat]}
        for seat in record.result.shown:
            allowed |= {card_str(c) for c in record.result.hole_cards[seat]}

        assert not (named - allowed), (
            f"seed {seed}: review context names {sorted(named - allowed)}"
        )


def test_context_builder_ignores_the_personas_field() -> None:
    """Enforcement is data flow, not prompting: mutating personas changes nothing."""
    record, _ = make_record(3)
    before = render_review_context(record)
    record.personas = {s: "SECRET_ARCHETYPE_MARKER" for s in record.personas}
    after = render_review_context(record)
    assert before == after
    assert "SECRET_ARCHETYPE_MARKER" not in after


def test_context_is_built_from_public_events_only() -> None:
    record, _ = make_record(9)
    record.events = [{"type": "action_taken", "seat": 1, "position": "SB",
                      "action": "bet", "to_amount": 999999, "pot_after": 0,
                      "is_all_in": False}]
    assert "999999" not in render_review_context(record)


# --------------------------------------------------------------------------
# The scrubber
# --------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "The BTN is a total nit and folds everything.",
    "That player is a calling station.",
    "As a language model I would raise here.",
    "The maniac in the CO will always bluff.",
    "This is a classic TAG line.",
])
def test_scrubber_drops_leaky_text(bad: str) -> None:
    assert _scrub(bad, DENY) is None


@pytest.mark.parametrize("fine", [
    "The CO check-raised the turn, which is hard to call profitably.",
    "You raised to 96 with top pair and left yourself no room.",
    "Calling keeps the button's worse hands in the pot.",
    "Once the small blind shoved you were priced in.",
])
def test_scrubber_keeps_legitimate_coaching(fine: str) -> None:
    assert _scrub(fine, DENY) == fine


def test_scrubber_uses_word_boundaries() -> None:
    """'nit' must not fire on 'initial' or 'community'."""
    assert _scrub("Your initial sizing was fine.", DENY) is not None
    assert _scrub("The community cards favour you.", DENY) is not None


def test_offending_note_is_dropped_but_the_rest_survives() -> None:
    coach = ReviewCoach(None, CFG, DENY)
    record, _ = make_record(5)
    view = coach._to_view({
        "verdict": "leak",
        "headline": "You over-committed with one pair.",
        "notes": [
            {"street": "flop", "text": "The CO is a calling station."},
            {"street": "turn", "text": "Betting again grows a pot you cannot win."},
        ],
        "better_line": "check the turn",
    }, record)
    assert view is not None
    assert len(view.notes) == 1
    assert "grows a pot" in view.notes[0].text


def test_a_leaky_headline_forces_the_fallback() -> None:
    coach = ReviewCoach(None, CFG, DENY)
    record, _ = make_record(5)
    view = coach._to_view({
        "verdict": "leak",
        "headline": "You misplayed against a maniac.",
        "notes": [],
        "better_line": "",
    }, record)
    assert view is None


# --------------------------------------------------------------------------
# The panel always renders something
# --------------------------------------------------------------------------

def test_deterministic_fallback_always_produces_a_review() -> None:
    coach = ReviewCoach(None, CFG, DENY)
    for seed in range(30):
        record, _ = make_record(seed)
        view = coach._deterministic(record)
        assert view.active
        assert view.headline
        assert view.notes
        assert _scrub(view.headline, DENY) is not None
        for note in view.notes:
            assert _scrub(note.text, DENY) is not None


def test_review_is_skipped_when_the_hero_folds_for_a_blind() -> None:
    coach = ReviewCoach(None, CFG, DENY)
    e = HandEngine(GameConfig(), NAMES, [300] * 6, button=0, hand_id=1, seed=1)
    e.start()
    stacks_before = {p.seat: p.stack + p.total_committed for p in e.state.players}
    while not e.is_complete():
        e.apply(Action.fold())
    result = e.result()
    record = HandRecord(
        hand_id=1, button=0, hero_seat=0, names={i: NAMES[i] for i in range(6)},
        events=[], public_events=[], decisions=[], personas={},
        result=result, stacks_before=stacks_before,
    )
    # The hero was the button here and folded for nothing.
    assert record.hero_invested == 0
    assert not coach._should_review(record)


# --------------------------------------------------------------------------
# The review is optional
# --------------------------------------------------------------------------

def test_review_can_be_turned_off() -> None:
    import dataclasses

    record, _ = make_record(7)
    on = ReviewCoach(None, CFG, DENY)
    assert on.enabled
    off = ReviewCoach(None, dataclasses.replace(CFG, review_enabled=False), DENY)
    assert not off.enabled
    assert not off._should_review(record)


def test_a_disabled_coach_never_starts_a_request() -> None:
    import dataclasses

    record, _ = make_record(8)
    coach = ReviewCoach(object(), dataclasses.replace(CFG, review_enabled=False),
                        DENY)
    coach.start(record)
    assert coach._task is None
    assert not coach.pending()


def test_the_switch_is_live() -> None:
    """Turning reviews off mid-session takes effect on the next hand."""
    record, _ = make_record(9)
    coach = ReviewCoach(None, dataclasses.replace(CFG, review_all=True), DENY)
    assert coach._should_review(record)
    coach.enabled = False
    assert not coach._should_review(record)
    coach.enabled = True
    assert coach._should_review(record)


def test_no_review_flag_and_env_var() -> None:
    from poker.__main__ import build_config, parse_args

    assert build_config(parse_args([])).review_enabled is True
    assert build_config(parse_args(["--no-review"])).review_enabled is False


# --------------------------------------------------------------------------
# The two-stage pipeline
# --------------------------------------------------------------------------

ANALYSIS = {
    "verdict": "leak",
    "summary": "Raising flop with TPTK at an SPR of 1.2 collapses your range advantage.",
    "findings": [
        {"street": "flop",
         "analysis": "Check-raising here isolates against a range that dominates "
                     "you; your equity when called is under 35%."},
        {"street": "turn",
         "analysis": "The blocker value of the ace is irrelevant once the SPR "
                     "commits you."},
    ],
    "better_line": "call 36 on the flop",
}

TRANSLATION = {
    "verdict": "leak",
    "headline": "Raising the flop built a pot you could not win.",
    "notes": [
        {"street": "flop", "text": "When you raise there, the only hands that "
                                   "call you are ones that beat you."},
        {"street": "turn", "text": "By then the pot was too big relative to your "
                                   "stack to get away."},
    ],
    "better_line": "call 36 on the flop",
}


class FakeClient:
    def __init__(self, analysis=None, translation=None,
                 fail_analysis=False, fail_translation=False):
        self.analysis = analysis if analysis is not None else ANALYSIS
        self.translation = translation if translation is not None else TRANSLATION
        self.fail_analysis = fail_analysis
        self.fail_translation = fail_translation
        self.calls: list[tuple[str, dict]] = []

    async def analyse(self, **kw):
        self.calls.append(("analyse", kw))
        if self.fail_analysis:
            raise RuntimeError("analyst down")
        return self.analysis

    async def translate(self, **kw):
        self.calls.append(("translate", kw))
        if self.fail_translation:
            raise RuntimeError("translator down")
        return self.translation


def run_pipeline(client, record, cfg=None):
    import asyncio

    coach = ReviewCoach(client, cfg or CFG, DENY)
    return asyncio.run(coach._run(record)), coach


def test_pipeline_runs_both_stages_and_returns_the_plain_language_version() -> None:
    record, _ = make_record(11)
    client = FakeClient()
    view, _ = run_pipeline(client, record)

    assert [name for name, _ in client.calls] == ["analyse", "translate"]
    assert view.headline == TRANSLATION["headline"]
    assert "SPR" not in view.headline
    assert view.verdict == "leak"
    assert len(view.notes) == 2
    assert view.notes[0].street == "flop"


def test_the_translator_sees_only_the_analysis() -> None:
    """It cannot reintroduce a leak, because it is never given the hand."""
    record, _ = make_record(12)
    client = FakeClient()
    run_pipeline(client, record)

    translate_kw = dict(client.calls[1][1])
    content = translate_kw["content"]
    # No board, no hole cards, no seat names -- just the analyst's JSON.
    assert content == json.dumps(ANALYSIS, indent=2)
    # Word boundaries: a bare substring scan matches "Th" inside "The".
    named = set(re.findall(r"\b([2-9TJQKA][shdc])\b", content))
    for cards in record.result.hole_cards.values():
        for card in cards:
            assert card_str(card) not in named


def test_analyst_and_translator_use_the_configured_models() -> None:
    record, _ = make_record(13)
    client = FakeClient()
    run_pipeline(client, record)
    assert client.calls[0][1]["model"] == CFG.analyst_model
    assert client.calls[1][1]["model"] == CFG.translator_model


def test_a_failed_translation_falls_back_to_the_analysts_own_words() -> None:
    record, _ = make_record(14)
    client = FakeClient(fail_translation=True)
    view, _ = run_pipeline(client, record)
    assert view.verdict == "leak"
    assert view.headline.startswith("Raising flop with TPTK")
    assert len(view.notes) == 2


def test_a_failed_analysis_falls_back_to_the_deterministic_summary() -> None:
    record, _ = make_record(15)
    client = FakeClient(fail_analysis=True)
    view, _ = run_pipeline(client, record)
    assert view.active
    assert view.headline
    assert "invested" in view.headline


def test_a_leaky_translation_falls_back_to_the_analysis() -> None:
    record, _ = make_record(16)
    leaky = dict(TRANSLATION, headline="You got outplayed by a calling station.")
    client = FakeClient(translation=leaky)
    view, _ = run_pipeline(client, record)
    assert "calling station" not in view.headline
    assert view.headline.startswith("Raising flop with TPTK")


def test_a_leaky_analysis_and_translation_fall_all_the_way_back() -> None:
    record, _ = make_record(17)
    client = FakeClient(
        analysis=dict(ANALYSIS, summary="The maniac had you crushed."),
        translation=dict(TRANSLATION, headline="The maniac had you crushed."),
    )
    view, _ = run_pipeline(client, record)
    assert "maniac" not in view.headline
    assert "invested" in view.headline


def test_verdict_survives_translation_unchanged() -> None:
    record, _ = make_record(18)
    for verdict in ("good", "ok", "leak", "big_leak"):
        client = FakeClient(
            analysis=dict(ANALYSIS, verdict=verdict),
            translation=dict(TRANSLATION, verdict=verdict),
        )
        view, _ = run_pipeline(client, record)
        assert view.verdict == verdict
