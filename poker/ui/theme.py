"""Named styles.  Every colour in the UI is referenced by name from here."""

from __future__ import annotations

from rich.theme import Theme

STYLES = {
    "felt": "green4",
    "felt.rail": "dark_green",
    "felt.inner": "on grey11",

    "chrome": "grey42",
    "title": "bold white",
    "subtle": "grey50",
    "muted": "grey35",

    "card.red": "bold red on white",
    "card.black": "bold grey11 on white",
    "card.blank": "on white",
    "card.back": "blue on deep_sky_blue4",
    "card.slot": "grey30",

    "seat": "grey70",
    "seat.name": "bold white",
    "seat.stack": "bold green3",
    "seat.acting": "bold yellow",
    "seat.folded": "grey30",
    "seat.allin": "bold magenta",
    "seat.winner": "bold green1",
    "seat.hero": "bold cyan",

    "badge.btn": "bold black on white",
    "badge.sb": "bold white on grey35",
    "badge.bb": "bold white on grey35",
    "badge.pos": "grey58",

    "pot": "bold yellow",
    "bet": "bold gold1",
    "bet.flash": "bold white on gold3",

    "action": "bold cyan",
    "action.key": "bold white",
    "action.bad": "bold white on red3",
    "prompt": "bold white",
    "prompt.hint": "grey50",
    "prompt.error": "bold red",

    "log": "grey62",
    "log.street": "bold grey74",
    "talk": "italic grey54",

    "review.good": "green3",
    "review.ok": "grey74",
    "review.leak": "yellow3",
    "review.big_leak": "red3",
    "review.head": "bold white",
    "review.street": "bold grey66",

    "banner.offline": "bold white on dark_red",
    "debug": "grey30",
}

THEME = Theme(STYLES)

VERDICT_STYLE = {
    "good": "review.good",
    "ok": "review.ok",
    "leak": "review.leak",
    "big_leak": "review.big_leak",
}

VERDICT_LABEL = {
    "good": "WELL PLAYED",
    "ok": "FINE",
    "leak": "LEAK",
    "big_leak": "BIG LEAK",
}
