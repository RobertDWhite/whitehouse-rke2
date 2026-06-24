# Building and Deploying KeepTrack

The KeepTrack source is not vendored here. Clone it locally first.
The `plugins-pro` submodule is private/SSH — skip it; the OSS build works without it.

## 0. Clone source (once)

```bash
git clone --depth 1 https://github.com/thkruz/keeptrack.space.git /tmp/keeptrack-src
cd /tmp/keeptrack-src
git submodule update --init --depth 1 src/engine/ootk

# Copy our nginx config and Dockerfile into the clone
cp /Users/robert/Documents/rke2/whitehouse-rke2/keeptrack/nginx.conf /tmp/keeptrack-src/
cp /Users/robert/Documents/rke2/whitehouse-rke2/keeptrack/Dockerfile /tmp/keeptrack-src/
```

## 1. Build the Docker image

The registry at `registry.white.fm:5000` is HTTP-only. Always build with
`docker buildx` for `linux/amd64` (required on Apple Silicon), and use
`--load` to write the image to the local Docker daemon rather than `--push`
(which fails against HTTP registries):

```bash
docker buildx build --platform linux/amd64 --load \
  -t registry.white.fm/keeptrack:12.1.3 \
  /tmp/keeptrack-src/
```

The build COPYs the local source and runs `npm run build` (rspack).
Output lands in `/app/dist`, copied into the nginx image.
Expect several minutes on first run due to npm install.

## 2. Push to the HTTP registry

After the image is loaded into the local daemon, push with plain `docker push`:

```bash
docker push registry.white.fm/keeptrack:12.1.3
```

Do **not** use `docker buildx build --push` — buildx bypasses the local daemon
and cannot authenticate against an HTTP registry without insecure-registry
configuration in the buildx builder.

## 3. Update the image tag in kustomization.yaml

Edit `kustomization.yaml` and change the `newTag` field to the new version:

```yaml
images:
  - name: registry.white.fm/keeptrack
    newTag: "12.1.3"   # <-- update this
```

Commit and push to `main`. ArgoCD will detect the change and roll out the new
image automatically (automated sync with selfHeal is enabled).

## Version reference

The current upstream version is tracked in
[package.json](https://github.com/thkruz/keeptrack.space/blob/main/package.json).
Match `newTag` to the `version` field there.
