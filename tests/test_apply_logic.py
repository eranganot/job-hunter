"""Tests for the apply-retry policy (Tier 2 #20)."""
import apply_engine


class TestRetryDecision:
    def test_manual_failures_never_retry(self):
        for ft in ("captcha", "login_wall", "form_validation"):
            assert apply_engine.retry_decision(ft, 1) == ("manual", 0)
            assert apply_engine.retry_decision(ft, 5) == ("manual", 0)

    def test_transient_failures_retry_with_backoff(self):
        a, b = apply_engine.retry_decision("timeout", 1)
        assert a == "retry" and b == 300
        a, b = apply_engine.retry_decision("network_error", 2)
        assert a == "retry" and b == 1200
        a, b = apply_engine.retry_decision("other", 1)
        assert a == "retry"

    def test_giveup_after_max_attempts(self):
        assert apply_engine.retry_decision("timeout", 3)[0] == "giveup"
        assert apply_engine.retry_decision("network_error", 4)[0] == "giveup"

    def test_backoff_is_capped(self):
        _, b = apply_engine.retry_decision("timeout", 9, max_attempts=100)
        assert b <= 6 * 3600

    def test_backoff_increases(self):
        _, b1 = apply_engine.retry_decision("timeout", 1, max_attempts=100)
        _, b2 = apply_engine.retry_decision("timeout", 2, max_attempts=100)
        assert b2 > b1
