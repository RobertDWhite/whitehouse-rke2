import json
from pathlib import Path
import unittest
from unittest.mock import patch

import posture_snapshot


class PartitionCoverageTest(unittest.TestCase):
    def setUp(self):
        fixture = Path(__file__).resolve().parents[1] / 'trivy-archive/testdata/partition-go.json'
        self.parts = json.loads(fixture.read_text())
        digest = self.parts[0]['report']['artifact']['digest']
        self.data = {'vulnerabilityreports': self.parts, 'pods': [{
            'metadata': {'namespace': 'test'}, 'spec': {},
            'status': {'phase': 'Running', 'containerStatuses': [{'imageID': 'fixture@' + digest}]},
        }]}

    def snapshot(self):
        with patch.object(posture_snapshot, 'resources', side_effect=lambda kind: self.data.get(kind, [])):
            return posture_snapshot.snapshot()

    def test_complete_partition_has_full_summary_and_ids(self):
        result = self.snapshot()
        self.assertEqual(1, result['matched_running_digests'])
        self.assertEqual(1, result['vulnerability_reports'])
        self.assertEqual(len(self.parts), result['vulnerability_report_objects'])
        self.assertEqual(0, result['incomplete_report_sets'])
        finding = result['current_image_findings'][0]
        self.assertEqual(2, finding['summary']['criticalCount'])
        self.assertEqual(['CVE-test-0', 'CVE-test-5'], finding['critical_ids'])

    def test_missing_part_is_not_coverage_or_partial_severity_totals(self):
        self.data['vulnerabilityreports'] = self.parts[1:]
        result = self.snapshot()
        self.assertEqual(0, result['matched_running_digests'])
        self.assertEqual(1, result['incomplete_report_sets'])
        self.assertEqual(1, len(result['unmatched_running_digests']))
        self.assertEqual([], result['current_image_findings'])


if __name__ == '__main__':
    unittest.main()
