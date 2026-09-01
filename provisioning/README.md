# Node provisioning (cloud-init)

Everything needed to stand up an RKE2 node from a fresh Ubuntu cloud image.
Public config lives here in plaintext; secrets are SOPS-encrypted with the
same age key the rest of this repo uses (see `../.sops.yaml`, first rule).

## Layout

```
cloud-init/user-data.yaml.tmpl        users, ssh key, ntp, rke2 install
cloud-init/network-config.yaml.tmpl   static IP, internal DNS only
cloud-init/meta-data.yaml.tmpl        instance-id / hostname
cloud-init/secrets.sops.env           RKE2_TOKEN (sops-encrypted)
nodes/_defaults.env                   shared: gateway, iface, server URL, ssh pubkey
nodes/<node>.env                      per-node: hostname, IP, server|agent
render.sh                             renders build/<node>/ and optional seed.iso
```

## Standing up a node (ESXi)

Base image (download once, keep on the datastore):
`https://cloud-images.ubuntu.com/releases/noble/release/ubuntu-24.04-server-cloudimg-amd64.ova`

This is the pre-installed **cloud image**, not the live-server installer ISO.
The installer ISO boots subiquity and ignores our NoCloud seed; the cloud
image boots straight into cloud-init and is up in seconds.

vCenter is defunct — each VM node runs on its own standalone ESXi host
(root creds = 1Password "esxi pw"; deploy.sh derives the host from the name):

| node VM      | ESXi host    | datastore (rebuild) | port group |
|--------------|--------------|---------------------|------------|
| rke2-node-11 | 10.100.1.11  | 1TB_SSD (tight!)    | Crypto     |
| rke2-node-13 | 10.100.1.13  | datastore02         | Crypto     |
| rke2-node-14 | 10.100.1.14  | Fanxiang S101 1T    | crypto     |
| rke2-node-15 | 10.100.1.15  | SSD (~full!)        | Crypto     |

(nodes 10 and 12 are physical — use the autoinstall path for those.)

One command (needs `OVA_PATH` in `nodes/_defaults.env` to exist):

```sh
cp nodes/rke2-node-XX.env.example nodes/rke2-node-16.env   # edit it
./deploy.sh rke2-node-16 --power-on
```

That renders the seed, **bakes an OVA pre-sized to DISK_GB** (make-ova.sh
patches the declared disk capacity — no slow post-import grow in ESXi;
cloud-init growpart expands the fs at first boot), imports it thin-provisioned,
sets CPU/RAM, attaches the seed ISO, and boots. Baked OVAs are cached per
size in `build/ova/`.

Manual path (ESXi UI), if you prefer:

1. `./render.sh rke2-node-16 --iso`, then upload `build/rke2-node-16/seed.iso`
   to the datastore.
2. ESXi UI → Create/Register VM → **Deploy a virtual machine from an OVF or
   OVA file** → pick a pre-sized OVA from `build/ova/` (run
   `./make-ova.sh <size-gb>` first) so the disk is right from the start.
   Leave any OVF user-data/public-key properties **blank** (the seed ISO
   provides everything).
3. Before first power-on, edit the VM: set CPU/RAM,
   network = the 10.99.5.0/24 port group, and add a CD/DVD drive pointing at
   `seed.iso` (Connect at power on).
4. Boot. cloud-init sets hostname/IP/keys, installs rke2, joins the cluster.
   Watch it join: `kubectl get nodes -w`.
6. `ssh <admin-user>@<ip>` with the ansible-node master key (username is
   `ADMIN_USER` in `cloud-init/secrets.sops.env`). `sudo` is passwordless.
7. Detach the seed ISO afterwards if you like — the instance-id in meta-data
   changes each render, so a leftover ISO won't re-run anything by itself.

## Alternative: normal live-server installer ISO

If you'd rather use the regular `ubuntu-24.04-live-server-amd64.iso` instead
of the OVA, render with `--autoinstall`:

1. `./render.sh rke2-node-16 --autoinstall` → upload
   `build/rke2-node-16/autoinstall-seed.iso` to the datastore.
2. Create a blank VM (Ubuntu 64-bit, disk sized how you want it — the
   installer WIPES it). Attach **two** CD drives: the ubuntu installer ISO
   (boot device) and the autoinstall-seed ISO.
3. Boot. The installer asks one `continue with autoinstall?` confirmation,
   then installs unattended, reboots, and first boot runs the same
   cloud-init config as the OVA path (keys, static IP, rke2 join).

Slower per node (~10 min full install vs ~30 s for the OVA), but no new
download needed. Both paths render from the same templates.

## Secrets

- Edit: `sops cloud-init/secrets.sops.env` (needs the age **private** key).
- The RKE2 join token comes from any existing server node:
  `/var/lib/rancher/rke2/server/node-token`.
- `render.sh` decrypts in-memory only; `build/` is gitignored and
  `build/*/user-data` contains the token — treat it accordingly.

## Break-glass reality check

This whole flow needs exactly two things when the cluster is down:
this repo and the **age private key**. The key lives in three places:
1Password ("SOPS age key — whitehouse-rke2", Private vault), the admin's Mac
(`~/Library/Application Support/sops/age/keys.txt`), and the cluster
(secret `argocd/sops-age`). On a fresh machine: paste it from 1Password
into that sops path and everything here works.

Baked-in lessons from past outages:
- NTP is forced on (VMware clock skew has future-dated rke2 certs before).
- DNS is 10.99.5.50/.51 only — never add a public fallback.
- The admin user gets passwordless sudo so recovery doesn't require nsenter pods.
