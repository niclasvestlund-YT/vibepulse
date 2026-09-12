#!/usr/bin/env python3
"""Guard the optional host advert and fail-closed firmware fallback."""

from pathlib import Path


root = Path(__file__).resolve().parents[1]
read = lambda path: (root / path).read_text(encoding="utf-8")

manifest = read("main/idf_component.yml")
cmake = read("components/torget_net/CMakeLists.txt")
discovery = read("components/torget_net/service_discovery.c")
http = read("components/torget_net/torget_http.c")
tokenserver = read("tools/tokenserver/tokenserver.py")
host_discovery = read("tools/tokenserver/discovery.py")

assert 'espressif/mdns: "1.11.3"' in manifest
assert '"service_discovery.c"' in cmake and " mdns " in cmake
assert 'mdns_query_ptr("_vibepulse", "_tcp"' in discovery
assert "TG_DISCOVERY_QUERY_MS 1500" in discovery
assert "NVS_ORIGIN_KEY" in discovery and "service_lkg" in discovery
assert "failed_now_locked" in discovery
assert "TG_SERVICE_SOURCE_CONFIGURED" in discovery
assert "torget_http_get_failover(configured_url, relay_url" in http
assert "torget_service_note_result" in http
# A feed switched on in LABS may have no compiled-in URL at all: discovery
# must still run, and the fallback is consulted only when it exists.
assert "!path || !configured_url" not in discovery
assert "return configured_url && bounded_copy(url, cap, configured_url);" in discovery
assert "(configured_url && strcmp(discovered, configured_url) == 0)" in http
assert "(!configured_url || strcmp(alternate, configured_url) != 0)" in http
# ...and with nothing advertised, the relay is still tried before giving up.
assert "return lan_url && torget_http_get(lan_url, buf, cap, len_out);" in http
assert http.count("torget_http_get_failover(configured_url, relay_url") == 3
# The two LABS-switchable pollers compile without their URL and poll anyway.
for poller, macro in (("components/app_tokens/github_net.c", "TK_GITHUB_URL"),
                      ("components/app_tokens/net.c", "TK_MAX_TRACKER_URL")):
    source = read(poller)
    assert f"#define {macro} NULL" in source, poller
    assert f"#ifdef {macro}" not in source, poller

assert "DiscoveryAdvertiser(log)" in tokenserver
assert "discovery.start(args.port)" in tokenserver
assert "discovery.stop()" in tokenserver
assert 'SERVICE_TYPE = "_vibepulse._tcp.local."' in host_discovery
assert "except ImportError:" in host_discovery
for forbidden in ("token", "quota", "account", "prompt", "project"):
    properties = host_discovery.split("properties={", 1)[1].split("}", 1)[0]
    assert forbidden not in properties.lower()

print("OK: mDNS discovery is bounded, optional, cached, and falls back closed")
