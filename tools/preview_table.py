"""Render a static frame at each layout size.  Dev tool, no engine or API."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rich.console import Console

from poker.engine.cards import parse_cards
from poker.engine.state import PlayerStatus, Street
from poker.ui.cards import probe_glyph_width
from poker.ui.model import ActionBar, InputLine, SeatView, TableView
from poker.ui.seats import COMPACT, FULL
from poker.ui.table import render_frame
from poker.ui.theme import THEME

probe_glyph_width()

seats = (
    SeatView(0, "YOU", 287, "BB", PlayerStatus.ACTIVE, is_hero=True,
             hole=tuple(parse_cards("Ah Qs")), face_up=True, street_bet=3),
    SeatView(1, "Tank", 96, "UTG", PlayerStatus.ALL_IN, street_bet=96),
    SeatView(2, "Deb", 268, "HJ", PlayerStatus.ACTIVE, street_bet=96),
    SeatView(3, "Walter", 412, "CO", PlayerStatus.FOLDED),
    SeatView(4, "Marcus", 301, "BTN", PlayerStatus.ACTIVE, thinking=True),
    SeatView(5, "Sofia", 455, "SB", PlayerStatus.ACTIVE, street_bet=60),
)

view = TableView(
    hand_id=142, street=Street.FLOP, board=tuple(parse_cards("As Kd 7c")),
    pot=174, seats=seats, hero_seat=0, button=4,
    session_profit=47, hands_played=38,
    action_bar=ActionBar(active=True, to_call=78, pot=174, can_fold=True,
                         can_call=True, can_raise=True, min_to=156, max_to=287,
                         stack=287),
    input_line=InputLine(active=True, text="raise 210", cursor=9,
                         preview="raise to $210",
                         hint="fold  ·  call 78  ·  raise <amt|half|pot>  ·  all-in 287"),
    log_lines=(
        ("PF  Tank raises to 12 · Sofia calls · Walter folds · Deb calls · YOU call", "log"),
        ("F   [As Kd 7c]  Deb checks · Tank bets 36 · Sofia raises to 60 …", "log"),
    ),
)

for layout in (FULL, COMPACT):
    print(f"\n=== {layout.name}  {layout.cols}x{layout.rows} ===")
    Console(theme=THEME, width=layout.cols + 1, height=layout.rows + 2).print(
        render_frame(view, layout, tick=0.4)
    )
