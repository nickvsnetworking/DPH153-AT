# DPH-153 DMI web console

A tiny on-box web UI for the DPH-153 PICO. It has a one-click **Bring cell up** button, shows
active alarms, GPS/GPIO state, and the full DMI parameter set, and lets you set any parameter.
It execs `ipa-dmi` locally, so it runs on the femto itself - no web server needed on the box
(this single binary IS the server).

## Bring the cell up (factory -> on-air, one click)

The green bar at the top of the dashboard is the whole bring-up sequence behind one button.
Its two fields default to the unit's current `hnbGwAddress` and `defaultNtpServer`; on a
factory unit type in your HNBGW and NTP IPs and press **GO**. `POST /cellup` then runs, on
the box, the same steps as `femto-cell-up.sh` does over SSH:

    set defaultNtpServer=<ntp>      (so the femto regenerates /tmp/ntp/ntp.conf)
    launch ntpd with the STOCK /tmp/ntp/ntp.conf  (only if none running - never kills a live one)
    set hnbGwAddress=<hnbgw>
    set administrativeState=UNLOCKED
    set rrmAdminState=LOC_UNLOCKED
    set csgAccessMode=CSG_ACCESS_MODE_OPEN_ACCESS
    action 2061 ; action 1216 ; action establishPermanentHnbGwConnection

It is idempotent - re-press it after a reboot. It reports each step, then watch the
**NTP/oscillator** panel: once reach hits 377 and the crystal disciplines (~10-15 min from a
cold start) the manager keys the RF and the HNB registers. The stock ntp.conf carries
`trap 127.0.0.1 port 8073`, which is how mgr_app learns the crystal converged - a custom
config without it syncs the clock but leaves the carrier off.

## Build

Go only. The PICO runs a 2.6.34 kernel, so a modern C cross-toolchain's glibc won't run there
("kernel too old"); Go's static binary does. CPU is ARMv6 soft-float (ARM1176) -> `GOARM=5`:

    CGO_ENABLED=0 GOOS=linux GOARCH=arm GOARM=5 go build -buildvcs=false -ldflags="-s -w" -o femtoui .

That is ~2 MB (Go runtime floor; it uses only raw `net`, no `net/http`/`regexp`/`fmt`).
Shrink with UPX (self-extracts into RAM at launch; verified on the 2.6.34 kernel):

    upx --best --lzma femtoui        # ~2.0 MB -> ~600 KB

## Deploy + run

Push it to the PICO and launch it. Use `ssh -T` (no PTY - a PTY does CRLF/XON-XOFF
translation on the pipe and corrupts the binary), and stop any running copy with
`pkill -x femtoui` (exact process-name match; `pkill -f femtoui` also matches your own SSH
command line and kills the session):

    cat femtoui | ssh -T <pico> 'pkill -x femtoui; sleep 1; cat > /tmp/femtoui; chmod +x /tmp/femtoui; PORT=8099 setsid /tmp/femtoui >/tmp/femtoui.log 2>&1 </dev/null &'

Then browse to `http://192.168.157.186:8099/`. (`<pico>` = the legacy-algo SSH one-liner from
the top-level README, key `cwmp_rce_key`.)

At ~600 KB it also fits the config partition (`/var/ipaccess`, ~2.7 MB free) if you want it to
survive reboots - drop it there and launch it from the `opmode.sh` boot hook.

## What it shows

Dashboard panels (top): System (uptime/load/free RAM), Identity (serial/MAC/variant/kernel),
NTP/oscillator (ntpq peer/reach/offset/jitter - the discipline health), GPS (receiver
running? fix state from `/tmp/gps`; AGPS source), Cell/UE (adminState, operationalState,
csgAccessMode, hnbGwAddress, close cause, active RABs).

Then: Active alarms (`apActiveAlarmsList`), Network Listen (macro cells heard - tied to the
"frequency error from NWL" alarm), Cell/RF/sync with per-row set boxes, All DMI parameters
with a live filter, and a tail of `/var/log/messages`.

Set box on the settable rows -> `ipa-dmi -c "set <attr>=<val>"` (Stdin is /dev/null so the
interactive `dmi>` prompt EOFs instead of hanging).

Data sources: `ipa-dmi -c getobj`, `/opt/ipaccess/bin/ntpq -pn`, `/proc/{uptime,loadavg,meminfo}`,
`/tmp/gps`, `/var/ipaccess/cisco/gps.cfg`, `hw_description.dat`, `/var/log/messages`.
Single file, Go stdlib only (raw `net`, no `net/http`).