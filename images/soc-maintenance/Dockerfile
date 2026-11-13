# Build-time dependencies, never apk add inside the running backup/mirror job.
FROM alpine:3.24@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf
RUN apk add --no-cache rsync wget ca-certificates \
    && rsync --version && wget --version
