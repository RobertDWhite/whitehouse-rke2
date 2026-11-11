# SOC maintenance image

Prebuilt rsync and GNU wget for photo backup jobs and Glacier mirroring. No
package installation is needed in running containers. Build for both node
architectures and pin the resulting manifest-list digest in consumers.

```sh
docker buildx build --platform linux/amd64,linux/arm64 --push \
  -t registry.internal.white.fm/soc-maintenance:YYYY-MM-DD images/soc-maintenance
```

Glacier manifests live in RobertDWhite/whitehouse-glacier; update that repository
as well when rebuilding this image. Do not change backup rsync arguments or add
`--delete` when updating dependencies.
