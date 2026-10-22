' House Screen: plays the Home Assistant dashboard stream full screen.
' Launch over ECP: POST /launch/dev (optionally ?contentId=<url-encoded HLS URL>).
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
    if args <> invalid and args.contentId <> invalid and args.contentId <> "" then return args.contentId
    return "https://screen.internal.white.fm/stream.m3u8"
end function
