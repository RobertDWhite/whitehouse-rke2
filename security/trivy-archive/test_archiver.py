import copy
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

    def test_go_partition_fixture_and_corruption(self):
        fixture = json.loads((Path(__file__).parent / 'testdata/partition-go.json').read_text())
        prefix = 'trivy-operator.aquasecurity.github.io/report-'
        complete, missing = archiver.complete_reports(fixture)
        self.assertEqual(missing, 0)
        self.assertEqual(len(complete), 1)
        self.assertEqual(len(complete[0]['report']['vulnerabilities']), 10)
        self.assertEqual(complete[0]['report']['vulnerabilities'][0]['score'], 9.8)
        for scenario in ('missing-part', 'orphan', 'mixed-generation', 'corruption', 'summary', 'duplicate', 'wrong-header', 'unknown-protocol'):
            with self.subTest(scenario=scenario):
                parts = copy.deepcopy(fixture)
                if scenario == 'missing-part':
                    parts.pop(0)
                elif scenario == 'orphan':
                    parts.pop()
                elif scenario == 'mixed-generation':
                    parts[0]['metadata']['annotations'][prefix+'generation'] = 'wrong'
                elif scenario == 'corruption':
                    parts[0]['report']['vulnerabilities'][0]['title'] = 'tampered'
                elif scenario == 'summary':
                    parts[-1]['report']['summary']['highCount'] += 1
                elif scenario == 'duplicate':
                    names = json.loads(parts[-1]['metadata']['annotations'][prefix+'parts'])
                    parts[-1]['metadata']['annotations'][prefix+'parts'] = json.dumps(names + [names[0]])
                elif scenario == 'wrong-header':
                    parts[0]['report']['artifact']['digest'] = 'wrong'
                else:
                    parts[-1]['metadata']['annotations'][prefix+'hash-format'] = 'unknown'
                self.assertEqual(archiver.complete_reports(parts), ([], 1))
        # API pruning of null object fields is not a content change.
        pruned = copy.deepcopy(fixture)
        for r in pruned:
            for v in r['report']['vulnerabilities']:
                if v.get('links') is None:
                    v.pop('links', None)
        self.assertEqual(len(archiver.complete_reports(pruned)[0]), 1)
        # Retry timestamps and JSON key ordering are not content changes.
        reordered = json.loads(json.dumps(fixture, sort_keys=True))
        for r in reordered:
            r['report']['updateTimestamp'] = '2026-10-26T12:00:00Z'
        self.assertEqual(len(archiver.complete_reports(reordered)[0]), 1)

    def test_partial_crd_cannot_satisfy_coverage(self):
        fixture = json.loads((Path(__file__).parent / 'testdata/partition-go.json').read_text())
        value = fixture[-1]['report']['artifact']['digest']
        for reports, expected in ((fixture, 0), (fixture[1:], 1), (fixture[:-1], 1)):
            with patch.object(archiver, 'items', side_effect=lambda path: reports if 'vulnerabilityreports' in path else []), \
                 patch.object(archiver, 'inventory', return_value=({'main': {value}, 'init': set(), 'ephemeral': set()}, 0)), \
                 patch.object(archiver, 'receipts', return_value=iter([])):
                metrics = archiver.coverage()
            self.assertEqual(metrics['trivy_inventory_crd_unmatched_digests{kind="main"}'], expected)
            self.assertEqual(metrics['trivy_archive_crd_incomplete_sets'], expected)

    def test_no_results_is_valid(self):
        report = {'SchemaVersion': 2, 'ArtifactName': 'empty'}
        self.assertEqual(archiver.parse(base64.b64encode(bz2.compress(json.dumps(report).encode()))), report)


if __name__ == '__main__':
    unittest.main()
