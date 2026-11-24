"""Launch finding F6 (2026-11-24): the limiter keys must be the caller, not
Railway's edge. Header shapes below are the ones observed on production."""
import app
import ratelimit


class _H:
    client_ip = app.Handler.client_ip
    limit_ip = app.Handler.limit_ip

    def __init__(self, headers, peer="100.64.0.7"):
        self.headers = headers
        self.client_address = (peer, 1234)


def test_through_cloudflare_the_real_ip_is_x_real_ip_not_the_edge():
    h = _H({"X-Forwarded-For": "172.69.109.182, 152.233.12.242",
            "X-Real-IP": "2a06:c701:4bf9:5300:e9be:1158:1cd8:48b9",
            "CF-Connecting-IP": "2a06:c701:4bf9:5300:e9be:1158:1cd8:48b9"})
    assert h.client_ip() == "2a06:c701:4bf9:5300:e9be:1158:1cd8:48b9"
    assert h.limit_ip() == "2a06:c701:4bf9:5300::/64"


def test_on_the_railway_host_a_forged_cf_header_is_ignored():
    h = _H({"X-Forwarded-For": "160.79.106.144, 152.233.40.2",
            "X-Real-IP": "160.79.106.144", "CF-Connecting-IP": "8.8.8.8"})
    assert h.client_ip() == "160.79.106.144"
    assert h.limit_ip() == "160.79.106.144"


def test_two_users_on_one_edge_node_get_two_buckets():
    a = _H({"X-Forwarded-For": "1.1.1.1, 152.233.40.1", "X-Real-IP": "31.168.1.1"})
    b = _H({"X-Forwarded-For": "1.0.0.1, 152.233.40.1", "X-Real-IP": "89.139.2.2"})
    assert a.limit_ip() != b.limit_ip()


def test_without_an_edge_the_last_forwarded_entry_then_the_peer():
    assert _H({"X-Forwarded-For": "6.6.6.6, 5.5.5.5"}).client_ip() == "5.5.5.5"
    assert _H({}, peer="127.0.0.1").client_ip() == "127.0.0.1"


def test_ip_key_groups_ipv6_by_64_and_tolerates_junk():
    assert ratelimit.ip_key("2001:db8:1:2:aaaa::1") == ratelimit.ip_key("2001:db8:1:2:bbbb::9")
    assert ratelimit.ip_key("10.0.0.1") == "10.0.0.1"
    assert ratelimit.ip_key("?") == "?"


def test_login_no_longer_keys_on_the_first_forwarded_entry():
    import inspect
    src = inspect.getsource(app)
    assert 'split(",")[0].strip() or (self.client_address[0]' not in src
    assert '_ipk = "ip:" + self.limit_ip()' in src
