#!/usr/bin/env python3
"""Move replicas of volumes without a disk selector off tagged (Immich-only) Longhorn disks.

Run once after setting Longhorn's allow-empty-disk-selector-volume=false, so the
replacement replicas can only schedule onto untagged disks.

Per volume, one at a time:
  1. Wait until the volume is healthy (all replicas healthy, none failed).
  2. A single-replica volume temporarily gets a second replica first, so it never
     has zero healthy copies.
  3. Delete one replica that sits on a tagged disk and wait for Longhorn to rebuild
     it elsewhere. If no allowed disk can take it (scheduling fails for
     SCHED_TIMEOUT), drop numberOfReplicas by one, never below 2.
  4. Restore the original replica count if it was raised in step 2.

Never deletes a replica unless the volume has at least one other healthy replica.
Usage: KUBECONFIG=... longhorn-evict-from-tagged-disks.py [--dry-run] [--limit N]
"""
import json
import subprocess
import sys
import time

NS = "longhorn-system"
POLL = 15
HEALTHY_TIMEOUT = 2 * 3600   # wait for a rebuild to finish (large volumes are slow)
SCHED_TIMEOUT = 10 * 60      # wait for a replacement replica to get a disk
DRY = "--dry-run" in sys.argv
LIMIT = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 0


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def k(*args, out=True, tries=8):
    """kubectl with retries: an apiserver restart briefly breaks discovery
    ("the server doesn't have a resource type") and must not kill a long run."""
    cmd = ["kubectl", *args]
    for attempt in range(tries):
        try:
            if out:
                return json.loads(subprocess.check_output(cmd + ["-o", "json"]))
            subprocess.check_call(cmd, stdout=subprocess.DEVNULL)
            return None
        except subprocess.CalledProcessError:
            if attempt == tries - 1:
                raise
            time.sleep(15 * (attempt + 1))


def tagged_disks():
    tagged = set()
    for n in k("get", "nodes.longhorn.io", "-n", NS)["items"]:
        for name, d in n["spec"]["disks"].items():
            if d.get("tags"):
                st = n["status"]["diskStatus"].get(name, {})
                tagged.add(st.get("diskUUID"))
    return tagged


def replicas(vol):
    items = k("get", "replicas.longhorn.io", "-n", NS, "-l", f"longhornvolume={vol}")["items"]
    return [r for r in items if not r["metadata"].get("deletionTimestamp")]


def volume(vol):
    return k("get", "volumes.longhorn.io", "-n", NS, vol)


def is_healthy(vol):
    """All replicas healthy and present; works for attached and detached volumes."""
    v = volume(vol)
    rs = replicas(vol)
    want = v["spec"]["numberOfReplicas"]
    good = [r for r in rs if r["spec"].get("healthyAt") and not r["spec"].get("failedAt")
            and r["spec"].get("nodeID")]
    if v["status"]["state"] == "attached":
        return v["status"]["robustness"] == "healthy" and len(good) >= want
    return len(good) >= want and len(rs) == len(good)


def scheduling_failed(vol):
    v = volume(vol)
    for c in v["status"].get("conditions", []):
        if c["type"] == "Scheduled" and c["status"] == "False":
            return True
    return any(not r["spec"].get("nodeID") for r in replicas(vol))


def wait_healthy(vol, timeout=HEALTHY_TIMEOUT):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if is_healthy(vol):
            return True
        time.sleep(POLL)
    return False


def set_replicas(vol, n):
    log(f"  {vol}: numberOfReplicas -> {n}")
    if not DRY:
        k("patch", "volumes.longhorn.io", "-n", NS, vol, "--type", "merge",
          "-p", json.dumps({"spec": {"numberOfReplicas": n}}), out=False)


def main():
    tagged = tagged_disks()
    vols = {v["metadata"]["name"]: v for v in k("get", "volumes.longhorn.io", "-n", NS)["items"]}
    todo = []
    for name, v in vols.items():
        if v["spec"].get("diskSelector"):
            continue  # Immich's own volumes: they belong on those disks
        bad = [r for r in replicas(name) if r["spec"].get("diskID") in tagged]
        if bad:
            todo.append((int(v["spec"]["size"]), name))
    todo.sort()
    log(f"{len(todo)} volumes have replicas on tagged disks{' (dry run)' if DRY else ''}")
    done = 0
    for _, vol in todo:
        if LIMIT and done >= LIMIT:
            break
        v = volume(vol)
        pvc = (v["status"].get("kubernetesStatus") or {}).get("pvcName", "?")
        log(f"== {vol} ({pvc}, {int(v['spec']['size']) // 2**30} GiB, "
            f"{v['spec']['numberOfReplicas']} replicas, {v['status']['state']})")
        if not wait_healthy(vol, timeout=20 * 60):
            log(f"  SKIP: not healthy after 20 min")
            continue
        original = v["spec"]["numberOfReplicas"]
        if original == 1:
            set_replicas(vol, 2)
            if not DRY and not wait_healthy(vol):
                log("  SKIP: second replica never became healthy; leaving at 2 replicas")
                continue
        while True:
            bad = [r for r in replicas(vol) if r["spec"].get("diskID") in tagged]
            if not bad:
                break
            healthy_others = [r for r in replicas(vol) if r not in bad
                              and r["spec"].get("healthyAt") and not r["spec"].get("failedAt")]
            if not healthy_others:
                log("  SKIP: no healthy replica off the tagged disks; not deleting")
                break
            r = bad[0]
            log(f"  delete replica {r['metadata']['name']} on {r['spec']['nodeID']}")
            if DRY:
                break
            k("delete", "replicas.longhorn.io", "-n", NS, r["metadata"]["name"], "--wait=false", out=False)
            time.sleep(POLL)
            t0 = time.time()
            while True:
                if is_healthy(vol):
                    log("  rebuilt, healthy")
                    break
                if time.time() - t0 > SCHED_TIMEOUT and scheduling_failed(vol):
                    n = volume(vol)["spec"]["numberOfReplicas"]
                    if n > 2:
                        log("  no allowed disk for another replica")
                        set_replicas(vol, n - 1)
                        t0 = time.time()
                        continue
                    log("  WARNING: cannot schedule a replacement and already at 2 replicas; leaving degraded")
                    break
                if time.time() - t0 > HEALTHY_TIMEOUT:
                    log("  WARNING: rebuild did not finish in time; moving on")
                    break
                time.sleep(POLL)
        if original == 1 and not DRY:
            set_replicas(vol, 1)
        done += 1
    log(f"finished: {done} volumes processed")


if __name__ == "__main__":
    main()
