# House Screen Roku channel

Sideloaded (developer mode) Roku channel that plays the ha-screen HLS stream (http://10.99.5.60, see service.yaml) full screen and
retries if it drops. Roku removed the "Play on Roku" channel that Home Assistant's Roku
integration used for URLs, so this is how HA puts the stream on the TV:
ECP `POST /launch/dev?contentId=live` (the stream) or `?contentId=blank` (a looping black clip,
used at the off times because the TV has no CEC or network control and an idle Roku shows its
screensaver).

Install (needs the Roku's developer-mode password):

    cd apps/home/ha-screen/roku-channel && zip -r /tmp/house-screen.zip manifest source components images video
    curl --digest -u rokudev:<password> -F mysubmit=Install -F archive=@/tmp/house-screen.zip http://<roku-ip>/plugin_install
