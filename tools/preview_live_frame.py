"""Render a real mid-hand frame through the actual view pipeline."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import random

from rich.console import Console

from poker.agents.fallback import decision_rng, local_decision
from poker.agents.personas import assign_seats
from poker.app.view import action_bar_from, build_view, pot_fractions
from poker.engine.state import GameConfig, Street
from poker.engine.table import Table
from poker.ui.cards import probe_glyph_width
from poker.ui.model import RaisePrompt, TableView
from poker.ui.seats import COMPACT, FULL
from poker.ui.table import render_frame
from poker.ui.theme import THEME

probe_glyph_width()
personas = assign_seats(random.Random(4), 0)
names = ["You"] + [""] * 5
for s, p in personas.items():
    names[s] = p.name

found = None
for seed in range(60):
    table = Table(GameConfig(), names, human_seat=0, session_seed=seed)
    for _ in range(25):
        e = table.start_hand()
        i = 0
        while not e.is_complete():
            seat = e.current_actor()
            if (seat == 0 and e.state.street >= Street.FLOP
                    and e.state.current_bet > 0):
                found = (e, table)
                break
            legal = e.legal_actions()
            persona = personas.get(seat) or personas[1]
            e.apply(local_decision(persona, e.observation(seat), legal,
                                   decision_rng(e.state.hand_id, seat,
                                                int(e.state.street), i)))
            i += 1
        if found:
            break
        table.settle(e.result())
    if found:
        break

assert found, "no suitable spot found"
e, table = found
legal = e.legal_actions()
pot = e.state.pot
half, tq, full = pot_fractions(legal, pot, e.state.current_bet)
view = build_view(e, table, TableView(), acting=0).with_(
    action_bar=action_bar_from(legal, pot),
    raise_prompt=RaisePrompt(active=True, typed="", min_to=legal.min_to,
                             max_to=legal.max_to, half_pot=half,
                             three_quarter_pot=tq, pot_size=full),
    log_lines=(("PF  Walter raises to 12 · Deb calls · YOU call", "log"),
               ("F   Deb checks · Sofia bets 24", "log")),
)
for layout in (FULL, COMPACT):
    print(f"\n=== {layout.name} ===")
    Console(theme=THEME, width=layout.cols + 1, height=layout.rows + 2).print(
        render_frame(view, layout, tick=0.4))
