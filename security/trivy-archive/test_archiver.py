import base64
import bz2
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import archiver


class ArchiveTests(unittest.TestCase):
    def test_recoverable_full_report_and_durable_receipt(self):
        value = 'sha256:' + 'a' * 64
        report = {'SchemaVersion': 2, 'ArtifactName': 'test',
                  'Metadata': {'RepoDigests': ['test@' + value], 'ImageID': 'sha256:' + 'b' * 64},
                  'Results': [{'Vulnerabilities': [{'Severity': 'HIGH'}],
                               'Secrets': [{'Match': 'fixture-only'}]}]}
        raw = base64.encodebytes(bz2.compress(json.dumps(report).encode()))
        with tempfile.TemporaryDirectory() as directory, patch.object(archiver, 'ROOT', Path(directory)):
            result = archiver.store({'metadata': {'name': 'job', 'uid': 'job-uid'},
                                    'status': {'completionTime': '2026-10-24T00:00:00Z'}},
                                   {'metadata': {'uid': 'pod-uid'}}, {'name': 'main'}, raw)
            self.assertEqual(gzip.decompress((Path(directory) / 'job-uid' / result['file']).read_bytes()), raw)
            self.assertEqual(result['digests'], [value])
            self.assertEqual(result['raw_sha256'], hashlib.sha256(raw).hexdigest())
            self.assertNotIn('fixture-only', json.dumps(result))
            self.assertEqual(list(archiver.receipts()), [result])
            self.assertEqual((Path(directory) / 'job-uid' / result['file']).stat().st_mode & 0o777, 0o600)

    def test_corrupt_output_retained_without_success_receipt(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(archiver, 'ROOT', Path(directory)):
            with self.assertRaises(ValueError):
                archiver.store({'metadata': {'name': 'job', 'uid': 'job-uid'}},
                               {'metadata': {'uid': 'pod-uid'}}, {'name': 'main'}, b'invalid')
            self.assertEqual(len(list(Path(directory).glob('*/*.gz'))), 1)
            self.assertEqual(list(archiver.receipts()), [])

    def test_inventory_includes_init_and_ephemeral_and_missing(self):
        value = 'sha256:' + 'a' * 64
        pod = {'spec': {'containers': [{'name': 'app'}, {'name': 'pending'}],
                        'initContainers': [{'name': 'init'}], 'ephemeralContainers': [{'name': 'debug'}]},
               'status': {'phase': 'Running', 'containerStatuses': [{'name': 'app', 'imageID': 'test@' + value}],
                          'initContainerStatuses': [{'name': 'init', 'imageID': 'containerd://' + value}],
                          'ephemeralContainerStatuses': [{'name': 'debug', 'imageID': 'test@' + value}]}}
        inventory, missing = archiver.inventory([pod])
        self.assertEqual(inventory, {k: {value} for k in ('main', 'init', 'ephemeral')})
        self.assertEqual(missing, 1)

    def test_archive_coverage_does_not_hide_missing_or_stale_crds(self):
        # A: fresh CRD only; B: fresh archive only; C: stale CRD/fresh archive;
        # D: no evidence. Count each running digest once per container kind.
        values = ['sha256:' + c * 64 for c in 'abcd']
        now = 200000.0
        reports = [{'report': {'artifact': {'digest': values[0]},
                               'updateTimestamp': '1970-01-03T07:33:20Z'}},
                   {'report': {'artifact': {'digest': values[2]},
                               'updateTimestamp': '1970-01-01T00:00:01Z'}}]
        archived = [{'digests': [values[i]], 'scan_completed': now,
                     'severity_counts': {'HIGH': 2}} for i in (1, 2)]
        def items(path):
            return reports if 'vulnerabilityreports' in path else []
        with patch.object(archiver, 'items', side_effect=items), \
             patch.object(archiver, 'inventory', return_value=({'main': set(values),
                         'init': {values[2]}, 'ephemeral': set()}, 1)), \
             patch.object(archiver, 'receipts', return_value=iter(archived)), \
             patch.object(archiver.time, 'time', return_value=now):
            metrics = archiver.coverage()
        self.assertEqual(metrics['trivy_inventory_unmatched_digests{kind="main"}'], 1)
        self.assertEqual(metrics['trivy_inventory_stale_digests{kind="main"}'], 0)
        self.assertEqual(metrics['trivy_inventory_crd_unmatched_digests{kind="main"}'], 2)
        self.assertEqual(metrics['trivy_inventory_crd_stale_digests{kind="main"}'], 1)
        self.assertEqual(metrics['trivy_inventory_fresh_archive_without_fresh_crd{kind="main"}'], 2)
        self.assertEqual(metrics['trivy_inventory_fresh_archive_without_fresh_crd{kind="init"}'], 1)
        self.assertEqual(metrics['trivy_inventory_fresh_archive_without_fresh_crd{kind="ephemeral"}'], 0)
        self.assertEqual(metrics['trivy_archive_latest_findings{severity="HIGH"}'], 4)

    def test_no_results_is_valid(self):
        report = {'SchemaVersion': 2, 'ArtifactName': 'empty'}
        self.assertEqual(archiver.parse(base64.b64encode(bz2.compress(json.dumps(report).encode()))), report)


if __name__ == '__main__':
    unittest.main()
