FROM germannewsmaker/myspeed:1.0.9@sha256:3a3e774b3f78d930a5a962d625b99bcb3d71730bfeb4a6b93e04fd38cfe7d9a9
# Upstream v1.0.9 checks bin/ookla but installs bin/speedtest, so it downloads
# the executable again at every start. Correct that check and install both
# configured providers in the image layer, using the upstream pinned versions.
RUN node -e 'const fs=require("fs"); const p="server/util/providers/loadOokla.js"; let s=fs.readFileSync(p,"utf8"); if(!s.includes("/ookla")) throw Error("upstream layout changed"); fs.writeFileSync(p,s.replace("/ookla","/speedtest"));' \
    && node -e 'Promise.all([require("./server/util/providers/loadOokla").load(),require("./server/util/providers/loadLibre").load()]).catch(e=>{console.error(e);process.exit(1)})' \
    && test -x /myspeed/bin/speedtest && test -x /myspeed/bin/librespeed-cli \
    && /myspeed/bin/speedtest --version && /myspeed/bin/librespeed-cli --version
