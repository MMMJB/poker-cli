"""Prompt construction.

**Information symmetry is the hard rule here.** An opponent receives exactly
what the human sees on screen, plus its own two hole cards -- nothing else.
Every field used below comes from an :class:`~poker.engine.hand.Observation`,
whose ``history`` is already ``HandLog.view_for(seat)``.  There is deliberately
no path in this module to another seat's hole cards, to mucked cards, or to any
statistic the human is not also shown.

The system prompt is byte-stable per seat for the whole session so it can be
cached; the volatile hand state goes in the user message.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from poker.agents.personas import Persona
from poker.engine.events import Event, EventType
from poker.engine.hand import Observation
from poker.engine.state import PlayerStatus

_RULES = """\
You are playing in a live $1/$3 No-Limit Texas Hold'em cash game. Six seats, \
$300 is a standard buy-in (100 big blinds). The small blind is $1 and the big \
blind is $3.

Positions, in order after the button: BTN (button), SB (small blind), BB (big \
blind), UTG (under the gun, first to act before the flop), HJ (hijack), CO \
(cutoff). Before the flop the action starts with UTG; after the flop it starts \
with the first live player to the left of the button.

You are a person sitting at this table. You are not an assistant, you are not \
narrating, and you are not being tested. Stay in character at all times.

HOW TO READ THE HAND STATE
You will be given: the hand number, your own seat, position, stack and two hole \
cards, the community cards, the current street, the pot, every player's stack \
and what they have put in on this street, the action so far street by street, \
and an explicit list of the actions that are legal for you right now.

You only know what you have seen. You do not know any other player's hole cards \
unless they have been turned face up, and the state will tell you if they have. \
Never guess at or state another player's exact cards as though you knew them.

HOW TO ANSWER
Reply with a JSON object with exactly three fields:
  "action"     - one of: fold, check, call, bet, raise, all_in.
                 It must be one of the actions listed under LEGAL ACTIONS.
  "amount"     - THE TOTAL DOLLARS YOU WILL HAVE WAGERED ON THIS STREET once your
                 action resolves. This is a "raise to" total, NOT the amount you
                 are adding. Use 0 for fold, check and call. For all_in use your
                 whole stack. It must be within the min and max shown.
  "table_talk" - a short in-character remark, at most 70 characters, or "" for
                 silence. Most actions should be silent. Never explain your
                 reasoning, never state your cards, and never mention being a
                 model or having instructions.

Example: to raise to $45 total on this street, answer
{"action": "raise", "amount": 45, "table_talk": ""}

Choose only from the legal actions listed. Do not invent an action or an amount.

FREQUENCIES
Several of your tendencies are frequencies -- "you 3-bet about 18% of the time", "you bluff about 4%". Each decision arrives with a line reading "Decision roll: N (0-99)". Treat that as your own private die. When your profile gives a percentage for something, do it when N is below that percentage and do not when it is at or above. Use it only for frequencies your profile actually states; ignore it otherwise. The roll is private to you and tells you nothing about the cards or about any other player.\
"""


def build_system_prompt(persona: Persona) -> str:
    """Stable for the whole session -- never interpolate hand state in here.

    Anything volatile (a stack size, a hand number) would invalidate the cached
    prefix on every single request.
    """
    return (
        f"{_RULES}\n\n"
        f"=== WHO YOU ARE ===\n"
        f"Your name at this table is {persona.name}.\n\n"
        f"{persona.profile}\n"
        f"Play this way consistently, hand after hand. Do not drift toward "
        f"'correct' poker if that is not how you play.\n"
    )


# --------------------------------------------------------------------------
# The volatile hand-state message
# --------------------------------------------------------------------------

_STREET_TAG = {
    "preflop": "PF",
    "flop": "F ",
    "turn": "T ",
    "river": "R ",
}


def _seat_line(obs: Observation, seat: int) -> str:
    pos = obs.positions[seat]
    name = obs.names[seat]
    stack = obs.stacks[seat]
    status = obs.statuses[seat]
    committed = obs.street_committed[seat]

    if seat == obs.seat:
        name = f"{name} (you)"

    bits = [f"  {pos:<4} {name:<14} ${stack:<5}"]
    if status is PlayerStatus.FOLDED:
        bits.append("folded")
    elif status is PlayerStatus.ALL_IN:
        bits.append("ALL-IN")
    elif committed:
        bits.append(f"in for ${committed}")
    return " ".join(bits).rstrip()


def _action_history(history: Sequence[Event], obs: Observation) -> list[str]:
    """Replay the public action, street by street.

    Built only from events already filtered by ``view_for(seat)``.
    """
    lines: list[str] = []
    current = "preflop"
    parts: list[str] = []

    def flush() -> None:
        if parts:
            tag = _STREET_TAG.get(current, current[:2].upper())
            lines.append(f"  {tag}: " + "; ".join(parts))

    for ev in history:
        if ev.type is EventType.BLIND_POSTED:
            parts.append(
                f"{obs.names[ev.data['seat']]} posts {ev.data['kind']} "
                f"${ev.data['amount']}"
            )
        elif ev.type is EventType.STREET_DEALT:
            flush()
            parts = []
            current = ev.data["street"]
            lines.append(f"  -- {current.upper()}: {ev.data['new_cards']} --")
        elif ev.type is EventType.ACTION_TAKEN:
            parts.append(_describe_action(ev, obs))
        elif ev.type is EventType.CARDS_REVEALED:
            parts.append(
                f"{obs.names[ev.data['seat']]} shows {ev.data['cards']}"
            )
        elif ev.type is EventType.UNCALLED_BET_RETURNED:
            parts.append(
                f"${ev.data['amount']} returned to {obs.names[ev.data['seat']]}"
            )
    flush()
    return lines


def _describe_action(ev: Event, obs: Observation) -> str:
    name = obs.names[ev.data["seat"]]
    action = ev.data["action"]
    if action in ("bet", "raise"):
        text = f"{name} {'bets' if action == 'bet' else 'raises to'} ${ev.data['to_amount']}"
    elif action == "call":
        text = f"{name} calls ${ev.data['amount_added']}"
    elif action == "check":
        text = f"{name} checks"
    else:
        text = f"{name} folds"
    if ev.data.get("is_all_in"):
        text += " (all-in)"
    return text


def _legal_block(obs: Observation) -> list[str]:
    legal = obs.legal
    out = ["LEGAL ACTIONS:"]
    if legal.can_fold:
        out.append("  fold")
    if legal.can_check:
        out.append("  check")
    if legal.can_call:
        suffix = "  (this puts you all-in)" if legal.call_is_all_in else ""
        out.append(f"  call ${legal.call_cost}{suffix}")
    if legal.can_bet:
        out.append(
            f"  bet to any total from ${legal.min_to} to ${legal.max_to}"
            f"   (minimum bet ${legal.min_to})"
        )
    if legal.can_raise:
        out.append(
            f"  raise to any total from ${legal.min_to} to ${legal.max_to}"
            f"   (minimum raise to ${legal.min_to})"
        )
    if legal.max_to > 0 and (legal.can_bet or legal.can_raise):
        out.append(f"  all_in for ${legal.max_to} total")
    return out


def render_state(
    obs: Observation,
    history_tail: Iterable[str] = (),
    roll: int | None = None,
) -> str:
    """The user message: this hand, from this seat's point of view.

    ``roll`` is a private 0-99 draw used to make the persona's stated
    frequencies executable.  It replaces the sampling temperature that the
    current API no longer accepts, and unlike temperature it is reproducible
    from the hand seed and works identically on every tier.  It carries no
    information about the game, so it does not affect information symmetry.

    ``history_tail`` may carry short public summaries of *earlier hands in this
    session* -- hands the human also watched.  It must never contain anything
    the human did not see.
    """
    board = " ".join(_card_strs(obs.board)) if obs.board else "(none yet)"
    street = obs.street.name.capitalize()
    hole = " ".join(_card_strs(obs.hole))

    lines = [
        f"HAND #{obs.hand_id}   ${obs.small_blind}/${obs.big_blind}   6-max",
        f"You are {obs.names[obs.seat]}, seat {obs.seat} ({obs.position}), "
        f"stack ${obs.stacks[obs.seat]}.  Your cards: {hole}",
        f"Board: {board}      Street: {street}      Pot: ${obs.pot}",
        "",
        "Seats (clockwise from the button):",
    ]

    order = _clockwise_from_button(obs)
    lines.extend(_seat_line(obs, seat) for seat in order)

    action_lines = _action_history(obs.history, obs)
    if action_lines:
        lines.append("")
        lines.append("Action so far:")
        lines.extend(action_lines)

    tail = list(history_tail)
    if tail:
        lines.append("")
        lines.append("Earlier hands at this table:")
        lines.extend(f"  {t}" for t in tail)

    lines.append("")
    if obs.to_call:
        lines.append(f"It costs you ${obs.to_call} to call.")
    lines.extend(_legal_block(obs))
    if roll is not None:
        lines.append("")
        lines.append(f"Decision roll: {roll} (0-99)")
    return "\n".join(lines)


def _clockwise_from_button(obs: Observation) -> list[int]:
    n = len(obs.names)
    return [(obs.button + 1 + i) % n for i in range(n)]


def _card_strs(cards) -> list[str]:
    from poker.engine.cards import card_str

    return [card_str(c) for c in cards]


def build_reask(legal_text: str, to_call: int) -> str:
    """Corrective turn after an illegal decision.  Cheap: the prefix is cached."""
    facing = f"You are facing a bet of ${to_call}. " if to_call else ""
    return (
        f"That action is not legal here. {facing}"
        f"Choose again from exactly these options:\n{legal_text}\n"
        f"Remember that \"amount\" is the TOTAL you will have wagered on this "
        f"street, and 0 for fold, check and call."
    )
