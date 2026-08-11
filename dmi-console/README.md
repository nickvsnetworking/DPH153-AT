# DPH-153 DMI web console

A tiny on-box web UI for the DPH-153 PICO. It shows active alarms, GPS/GPIO state, and the
full DMI parameter set, and lets you set any parameter. It execs `ipa-dmi` locally, so it
runs on the femto itself - no web server needed on the box (this single binary IS the server).

## Build

Go only. The PICO runs a 2.6.34 kernel, so a modern C cross-toolchain's glibc won't run there
("kernel too old"); Go's static binary does. CPU is ARMv6 soft-float (ARM1176) -> `GOARM=5`:

    CGO_ENABLED=0 GOOS=linux GOARCH=arm GOARM=5 go build -buildvcs=false -ldflags="-s -w" -o femtoui .

That is ~2 MB (Go runtime floor; it uses only raw `net`, no `net/http`/`regexp`/`fmt`).
Shrink with UPX (self-extracts into RAM at launch; verified on the 2.6.34 kernel):

    upx --best --lzma femtoui        # ~2.0 MB -> ~600 KB

## Deploy + run

Push it to the PICO and launch it:

    cat femtoui | ssh <pico> 'cat > /tmp/femtoui; chmod +x /tmp/femtoui; PORT=8099 nohup /tmp/femtoui >/tmp/femtoui.log 2>&1 &'

Then browse to `http://192.168.157.186:8099/`. (`<pico>` = the legacy-algo SSH one-liner from
the top-level README, key `cwmp_rce_key`.)

At ~600 KB it also fits the config partition (`/var/ipaccess`, ~2.7 MB free) if you want it to
survive reboots - drop it there and launch it from the `opmode.sh` boot hook.

## What it shows

- Active alarms (`apActiveAlarmsList`, e.g. "Unable to correct frequency error from NWL")
- GPS / GPIO (GPS power via `GPIO_GPS_ON_OFF`, crystal discipline method, NTP server)
- Cell / RF / sync (adminState, csgAccessMode, hnbGwAddress, UARFCN, ...)
- All DMI parameters with a live client-side filter
- A set box on each row -> `ipa-dmi -c "set <attr>=<val>"` (Stdin is /dev/null, so the
  interactive `dmi>` prompt EOFs instead of hanging)

Single file, Go stdlib only.
