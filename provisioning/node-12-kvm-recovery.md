# rke2-node-12 — out-of-band recovery via KVM

node-12 has a **GL.iNet Comet KVM** (GLKVM, PiKVM/kvmd-based) wired to its
HDMI + USB. Use it when the node is `NotReady` and SSH/ping are dead.

| | |
|---|---|
| Web UI | https://kvm.internal.white.fm (via the gateway; device is 10.100.2.85, Pro XG switch port 2) |
| Credentials | 1Password → "GLKVM Comet (node-12)" (never in this repo) |
| Power control | **none** (no ATX board). Reboot = magic SysRq, or a physical button press |
| Root shell on the KVM | Web UI → Terminal, or ttyd at `/extras/webterm/ttyd/ws` |

Admin passwords must be **5–63 characters**. A shorter one is silently
rejected and the device stays in first-run setup (`/api/init/is_inited` → `false`).

## Triage

1. Open the web UI and look at the console. Or use the API:
   - `POST /api/auth/login` (form `user`, `passwd`) → `auth_token` cookie.
   - `GET /api/streamer/snapshot` returns **503 unless a client is streaming**,
     so hold `wss://kvm.internal.white.fm/api/ws?stream=1` open while you take the snapshot.
2. Press Enter, then **Ctrl+Alt+F2**:
   - Login prompt appears → userspace is alive. Log in and investigate.
   - Blank tty with only a cursor → kernel alive, userspace wedged. Reboot it.
   - Nothing changes → hard hang. Physical power cycle.

## Reboot from the keyboard (magic SysRq)

Ubuntu's default `kernel.sysrq=176` allows sync, remount-ro and reboot.
SysRq is `PrintScreen` in the KVM's key names. Send these one at a time,
a few seconds apart:

- **Alt+PrintScreen+S** (sync)
- **Alt+PrintScreen+U** (remount read-only)
- **Alt+PrintScreen+B** (reboot)

Nothing is echoed (console loglevel hides it). The node normally boots,
rejoins the cluster, and reports `Ready` within a minute or two.

API form: `POST /api/hid/events/send_key?key=AltLeft&state=true`, then the
same for `PrintScreen` and `KeyS`, then release them in reverse order with `state=false`.

## After it's back

- **Post-mortem logs:** node-12's SSH host key changed when it was rebuilt,
  so use a debug pod:
  `kubectl debug node/rke2-node-12 --profile=sysadmin --image=busybox:1.36 -- chroot /host journalctl -b -1 ...`
  Delete the `node-debugger-*` pod in `default` afterwards.
- **RX888:** if `lsusb` shows no Cypress `04b4:*` device and dmesg has
  `usb 1-2: device not accepting address` / `error -110`, the SDR is wedged.
  A software port reset doesn't power-cycle it, so **physically replug it**,
  into a USB 3 (blue) port. The UPS and the KVM sit behind hub `1-1`, so leave those alone.
- **openclaw-0** can sit in `Init` until Longhorn finishes any offline
  rebuild of `openclaw-data` on another node. Waiting it out is fine.

## Incident log

- **2026-10-12 → 10-15.** For about a day, a Longhorn volume mounted on node-12
  threw unrecoverable read errors (RocksDB/ArangoDB). Then at 08:43 UTC every
  Longhorn volume on the node dropped at once, because iSCSI to the local
  instance-manager failed. The filesystems went read-only and userspace hung,
  with kernel still up. Recovered via KVM + SysRq reboot. The RX888 came back
  wedged on USB port 1-2 and needed a physical replug.
