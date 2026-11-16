"""
dailymsg.py - the message a user gets when a search brings new picks, in Noa's voice.

It is the message people get most. Until 2026-11-15 it was a system log signed
by Noa: "🎯 3 new role(s) for review", subject "Job Hunter Notification", link
re-check counts, raw errors, and a daily "no new matches today". The copy here
was approved in the lifecycle-emails review (13 Nov): Noa says what she found
and why the best ones fit; a day with nothing new sends nothing.

Pure functions, no I/O: app.py decides who gets what and sends it.
"""
import re

from lifecycle import NOA_SIG

FOOTER = "Choose how I reach you in Settings → Notifications."


def first_sentence(text: str, limit: int = 180) -> str:
    """The first sentence of Noa's "why this fits", for one line under a job."""
    text = " ".join((text or "").split())
    if not text:
        return ""
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    out = m.group(1) if m else text
    if len(out) > limit:
        out = out[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return out


def _role(title: str, company: str) -> str:
    title, company = (title or "").strip(), (company or "").strip()
    return "%s at %s" % (title, company) if company else title


def _letter(first_name: str, top: list, total: int, link: str, words) -> dict:
    total = max(int(total or 0), 1)
    top = [(t, c, w) for t, c, w in (top or []) if (t or "").strip()][:3]
    hi = "Hi %s," % first_name.strip() if (first_name or "").strip() else "Hi,"
    best = _role(top[0][0], top[0][1]) if top else ""
    push, subject, opening = words(total, best)
    if total == 1:
        closing = "Swipe right if you like it:"
    else:
        rest = total - len(top)
        closing = ("%d more %s waiting. " % (rest, "is" if rest == 1 else "are") if rest > 0 and top else "") \
            + "Swipe right on the ones you like:"
    blocks = []
    for title, company, why in top:
        reason = first_sentence(why)
        blocks.append(_role(title, company) + ("\n" + reason if reason else ""))
    middle = ("\n\n".join(blocks) + "\n\n") if blocks else ""
    body = "%s\n\n%s\n\n%s%s\n\n%s\n\n%s\n\n%s" % (hi, opening, middle, closing, link, NOA_SIG, FOOTER)
    return {"push": push, "subject": subject, "body": body}


def _today_words(total, best):
    if total == 1:
        return (("Today's pick is in: %s. It's very you." % best) if best else "Today's pick is in.",
                ("Today's pick: %s" % best) if best else "Today's pick is in",
                "Today's pick is in:")
    return (("Today's %d are in. %s is very you." % (total, best)) if best else "Today's %d are in." % total,
            ("Today's picks: %s + %d more" % (best, total - 1)) if best else "Today's %d picks are in" % total,
            "Today's %d are in. The best of them:" % total)


def _more_words(total, best):
    if total == 1:
        return (("I found 1 more for you: %s." % best) if best else "I found 1 more for you.",
                ("1 more pick: %s" % best) if best else "1 more pick",
                "Your search found 1 more:")
    return (("I found %d more for you. %s is very you." % (total, best)) if best else "I found %d more for you." % total,
            ("%d more picks: %s + %d more" % (total, best, total - 1)) if best else "%d more picks" % total,
            "Your search found %d more. The best of them:" % total)


def new_picks(first_name: str, top: list, total: int, link: str) -> dict:
    """{"push", "subject", "body"} for `total` new picks.

    `top` is the best of them, best first: (title, company, why_relevant); up to
    three are named. `total` is how many came in, so the numbers are the user's own.
    """
    return _letter(first_name, top, total, link, _today_words)


def more_picks(first_name: str, top: list, total: int, link: str) -> dict:
    """The same letter for the picks a search the USER started brought
    (2026-11-16: up to 7 per search, on top of the day's). It does not say
    "today's" - those are separate."""
    return _letter(first_name, top, total, link, _more_words)
