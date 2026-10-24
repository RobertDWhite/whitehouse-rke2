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

    def test_no_results_is_valid(self):
        report = {'SchemaVersion': 2, 'ArtifactName': 'empty'}
        self.assertEqual(archiver.parse(base64.b64encode(bz2.compress(json.dumps(report).encode()))), report)


if __name__ == '__main__':
    unittest.main()
