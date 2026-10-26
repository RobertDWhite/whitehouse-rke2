#!/usr/bin/env python3
"""Compare real renders without printing or saving decrypted manifests."""
import argparse
import io
import os
from pathlib import Path
import shlex
import subprocess
import tarfile
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-pod", required=True)
    parser.add_argument("--candidate-pod", required=True)
    parser.add_argument("--namespace", default="argocd")
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    source = args.source.resolve()
    workloads = ["apps/misc/freshrss", "apps/misc/misp", "apps/misc/wallabag"]
    inputs = workloads + ["argocd/Chart.yaml", "argocd/Chart.lock", "argocd/values/argocd-values.yaml"]
    paths = subprocess.check_output(["git", "-C", str(source), "ls-files", "-z", "--", *inputs]).decode().split("\0")
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as bundle:
        for name in filter(None, paths):
            path = source / name
            if not path.resolve().is_relative_to(source):
                raise RuntimeError("Source file resolves outside checkout")
            bundle.add(path, arcname=name, recursive=False)
    root = "/tmp/argocd-security-validation-" + uuid.uuid4().hex
    private = Path.home() / "Library/Application Support/whitehouse-security/argocd-render-validation"
    private.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(private, 0o700)

    def run(pod, command, payload=None):
        result = subprocess.run(["kubectl", "exec", "-i", "-n", args.namespace, pod,
                                 "-c", "repo-server", "--", "/bin/bash", "-o", "pipefail", "-ec", command],
                                input=payload, capture_output=True)
        if result.returncode:
            log = private / (uuid.uuid4().hex + ".stderr")
            fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(result.stderr)
            raise RuntimeError("Validation command failed; private diagnostics: " + str(log))
        return result.stdout

    pods = [args.baseline_pod, args.candidate_pod]
    prepared = []
    try:
        for pod in pods:
            run(pod, "mkdir -p " + shlex.quote(root))
            prepared.append(pod)
            run(pod, "tar --no-same-owner --no-same-permissions -xzf - -C " + shlex.quote(root), archive.getvalue())
        for workload in workloads:
            command = "kustomize build --enable-alpha-plugins --enable-exec " + shlex.quote(root + "/" + workload) + " | sha256sum"
            baseline, candidate = [run(pod, command).split()[0] for pod in pods]
            if baseline != candidate:
                raise RuntimeError("Rendered resources differ: " + workload)
            print("Matching render:", workload, flush=True)
        chart = shlex.quote(root + "/argocd")
        helm_env = "export HELM_CACHE_HOME=" + shlex.quote(root + "/helm/cache") + " HELM_CONFIG_HOME=" + shlex.quote(root + "/helm/config") + " HELM_DATA_HOME=" + shlex.quote(root + "/helm/data") + "; "
        run(args.baseline_pod, helm_env + "helm repo add security-fixture https://argoproj.github.io/argo-helm >/dev/null; cd " + chart + " && helm dependency build >/dev/null")
        charts = run(args.baseline_pod, "tar -czf - -C " + chart + " charts")
        run(args.candidate_pod, "tar --no-same-owner -xzf - -C " + chart, charts)
        command = helm_env + "helm template argocd " + shlex.quote(root + "/argocd/charts/argo-cd-10.11.1.tgz") + " --namespace argocd -f " + shlex.quote(root + "/argocd/values/argocd-values.yaml") + " | sha256sum"
        baseline, candidate = [run(pod, command).split()[0] for pod in pods]
        if baseline != candidate:
            raise RuntimeError("Helm render differs; investigate before rollout")
        print("Matching Helm render: Argo chart and custom values", flush=True)
    finally:
        for pod in prepared:
            try:
                run(pod, "rm -rf -- " + shlex.quote(root))
            except RuntimeError:
                print("Temporary fixture cleanup requires attention for pod " + pod)


if __name__ == "__main__":
    main()
