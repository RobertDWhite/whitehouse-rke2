# MySpeed prebuilt providers

The upstream 1.0.9 image downloads Ookla and LibreSpeed into its writable layer
at startup. Its Ookla existence check also tests `bin/ookla` despite installing
`bin/speedtest`, forcing repeated downloads. This derivative fixes that check
and installs the upstream-pinned provider versions while building, preserving
the existing server and data format. It does not migrate or repair the database.

Build for linux/amd64 and linux/arm64, push to the internal registry, and update
the deployment with the resulting manifest-list digest. The Dockerfile checks
both binaries execute successfully without running a network speed test.

```sh
docker buildx build --platform linux/amd64,linux/arm64 --push \
  -t registry.internal.white.fm/myspeed:1.0.9-prebuilt images/myspeed
```
