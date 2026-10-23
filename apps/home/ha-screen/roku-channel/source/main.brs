' House Screen: plays the Home Assistant dashboard stream full screen.
' Launch over ECP: POST /launch/dev (optionally ?contentId=<url-encoded HLS URL>).
' contentId=blank shows a black screen instead (the TV can't be switched off remotely, and an
' idle Roku would fall back to its screensaver); contentId=live goes back to the stream.
sub Main(args as Dynamic)
    screen = CreateObject("roSGScreen")
    port = CreateObject("roMessagePort")
    screen.SetMessagePort(port)
    scene = screen.CreateScene("PlayerScene")
    screen.Show()
    scene.url = streamUrl(args)

    ' Re-launching the channel while it runs (deep link) arrives as an input event.
    input = CreateObject("roInput")
    input.SetMessagePort(port)

    while true
        msg = Wait(0, port)
        if Type(msg) = "roSGScreenEvent" and msg.IsScreenClosed() then return
        if Type(msg) = "roInputEvent" and msg.IsInput() then
            scene.url = streamUrl(msg.GetInfo())
        end if
    end while
end sub

function streamUrl(args as Dynamic) as String
    if args <> invalid and args.contentId <> invalid and args.contentId <> "" and args.contentId <> "live" then return args.contentId
    return "http://10.99.5.60/stream.m3u8"
end function
