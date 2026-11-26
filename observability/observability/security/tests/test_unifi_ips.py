import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from unifi_ips import normalize


class NormalizeTests(unittest.TestCase):
    def event(self, parameters):
        return {
            "id": "threat-123", "timestamp": 1000, "event": "THREAT_BLOCKED",
            "severity": "VERY_HIGH", "parameters": parameters,
        }

    def test_public_source_uses_local_db_without_emitting_address(self):
        ip = "1.1.1.1"
        geo_db = {ip: {"country": {"iso_code": "AU"}, "location": {"latitude": -33.8, "longitude": 151.2}}}
        _, timestamp, line = normalize(self.event({"SRC_IP": {"id": ip}}), geo_db)
        self.assertEqual(timestamp, 1000)
        self.assertIn("country=AU", line)
        self.assertIn("lat=-33.8000 lon=151.2000 action=blocked", line)
        self.assertIn("scope=public", line)
        self.assertNotIn(ip, line)

    def test_local_multicast_and_named_clients_are_not_looked_up(self):
        for parameters in ({"SRC_IP": {"id": "10.100.1.30"}}, {"SRC_IP": {"id": "224.0.0.252"}},
                           {"SRC_IP": {"id": "::ffff:10.100.1.30"}}, {"SRC_CLIENT": {"name": "internal"}}):
            with self.subTest(parameters=parameters):
                _, _, line = normalize(self.event(parameters), None)
                self.assertIn("scope=local", line)
                self.assertIn("lat=- lon=-", line)

    def test_missing_source_is_not_inferred_from_destination(self):
        _, _, line = normalize(self.event({"DST_IP": {"id": "1.1.1.1"}}), None)
        self.assertIn("scope=unknown", line)
        self.assertIn("country=-", line)
