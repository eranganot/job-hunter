"""A link the insert check skips is logged at INFO, whatever the site's error text
says (2026-11-13: "[SSL: SSLV3_ALERT_HANDSHAKE_FAILURE] ... handshake failure"
from careers.genworth.com - a dead domain - graded ERROR by the print shim's
word test and reached the owner as a Sentry alert)."""
import logging
import pathlib

import log

MSG = ("[run-search] skipped at insert (unknown): network: [SSL: SSLV3_ALERT_HANDSHAKE_FAILURE] ssl/tls alert "
       "handshake failure (_ssl.c:1018) - https://careers.genworth.com/job/richmond/director-product-management/391/58000000000")


def test_the_old_print_graded_this_line_error():
    # Why the explicit level is needed: the shim reads "failure" as an error.
    assert log._level_for(MSG) == logging.ERROR


def test_the_insert_skip_is_logged_at_info_explicitly():
    src = (pathlib.Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")
    assert 'print(f"[run-search] skipped at insert' not in src
    assert '_log.get("run-search").info("[run-search] skipped at insert (%s): %s - %s"' in src


def test_an_info_record_with_failure_in_it_stays_info(caplog):
    with caplog.at_level(logging.INFO):
        log.get("run-search").info("[run-search] skipped at insert (%s): %s - %s", "unknown",
                                   "network: ssl/tls alert handshake failure", "https://x")
    assert [r.levelno for r in caplog.records if "skipped at insert" in r.getMessage()] == [logging.INFO]
