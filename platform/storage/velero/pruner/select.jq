# Prints "<backup-name>\t<backup-uid>" for every scheduled backup that can go.
#
# Input: `kubectl get backups.velero.io -o json`
# Args:  $keep (number), $pvbs (podvolumebackups list), $bsls (BSL list)
#
# A backup is kept if ANY of these hold:
#   - it is one of the newest $keep usable (Completed/PartiallyFailed) backups
#     of its schedule - that is "n and n-1" with the default keep=2;
#   - it holds the newest successful copy of some volume. Since the Velero
#     1.18 upgrade most nightly runs are PartiallyFailed, so the last good copy
#     of a database can easily be several backups old; deleting it would leave
#     that volume with no backup at all;
#   - it is not finished yet (InProgress, Queued, Finalizing, ...).
# Only backups on an Available location are considered: a DeleteBackupRequest
# against an unreachable object store just hangs in "Deleting".

def usable: .status.phase == "Completed" or .status.phase == "PartiallyFailed";
def finished: usable or .status.phase == "Failed" or .status.phase == "FailedValidation";

($bsls[0].items | map(select(.status.phase == "Available") | .metadata.name)) as $avail
| [.items[]
   | select(.metadata.labels["velero.io/schedule-name"] != null)
   | select(.spec.storageLocation as $l | $avail | index($l))] as $sched
| ($sched | map(select(usable) | .metadata.name)) as $names

# Newest successful copy per volume, counting only finished, restorable
# backups - a volume that succeeded inside tonight's still-running backup is
# not safe yet, that run may still end up Failed. Pod names are reduced to
# their workload (drop the ReplicaSet/Job hash suffixes) so the key survives
# pod restarts.
| ($pvbs[0].items
   | map(select(.status.phase == "Completed")
         | {b: .metadata.labels["velero.io/backup-name"],
            t: .metadata.creationTimestamp,
            k: "\(.spec.pod.namespace)/\(.spec.pod.name
                  | sub("-[a-z0-9]{8,10}-[a-z0-9]{5}$"; "")
                  | sub("-[a-z0-9]{5}$"; ""))/\(.spec.volume)"}
         | select(.b as $b | $names | index($b)))
   | group_by(.k) | map(max_by(.t).b) | unique) as $protected

| ($sched
   | group_by(.metadata.labels["velero.io/schedule-name"])
   | map(map(select(usable))
           | sort_by(.metadata.creationTimestamp) | reverse
           | .[:$keep] | map(.metadata.name))
   | add // []) as $kept

| $sched[]
| select(finished)
| .metadata.name as $n
| select(($kept | index($n)) == null and ($protected | index($n)) == null)
| "\($n)\t\(.metadata.uid)"
