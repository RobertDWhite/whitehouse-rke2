# Build-time dependencies, never apk add inside the running backup/mirror job.
FROM alpine:3.22@sha256:14358309a308569c32bdc37e2e0e9694be33a9d99e68afb0f5ff33cc1f695dce
RUN apk add --no-cache rsync wget ca-certificates \
    && rsync --version && wget --version
