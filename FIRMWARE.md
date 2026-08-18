# Building the custom firmware

`rmm-selfclean.sdp` is a hook-only ip.access `.SDP` that the DPH-153's stock `swdl_client`
accepts and runs as root. This is how it's made.

## Why it works

The firmware-download path (`swdl_client` -> `post_swdl_hook`) supports a package type
`0x5007` ("sdphook") whose payload is a shell script. On this build **image signing is off**
(`verifyflash` disabled), so the package needs no signature - only the three internal CRCs
and a well-formed header. `post_swdl_hook` **sources the script as root**, before its own
destructive teardown, so a trailing `exit 0` runs our code and skips the rest. It never
writes a firmware bank, so the active image stays verifiable.

## The .SDP format

`sdp_pack.py` writes the whole thing. Big-endian throughout, CRCs = `zlib.crc32`:

    HEADER (298 bytes)
      ' SDP'  totalheaderlength(4)  unused(4)  totalfilelength(4)
      sdpversionid(120)  hwcompReserved(20)
      hwcomptablelength(BE16)=0        (0 => skip the hw-compat table)
      itemindexlength(BE16)=138        (per-entry bytes => exactly one index entry)
      index entry (138B): id, textdesc(64), buildtime(12), builddate(14), userinit(10),
        versionid(20), lengthbytes(4), loadaddr(4), execoffset(4), offsettostart(4)=298
      sdpheadercrc(4) = crc32(header so far)
    DATA
      'IMAG'  imgidType(BE16)=0x5007   <script bytes>
      itemcrc(4) = crc32(script bytes only)
    TRAILER
      filecrc(4) = crc32(everything before it)

The three fields that make the unmodified parser accept it: `itemindexlength=138` (one
entry), `imgidType=0x5007` (sdphook), and `offsettostart=298` so the `IMAG` data blob lands
exactly where `getdata` expects it. Verified against `swdl_client -verbose 2`: "Found sdp_hook
script image" / "Image sdphook accepted" / "Successfully downloaded", no CRC or parser errors.

## Build it

    python3 sdp_pack.py selfclean_hook.sh rmm-selfclean.sdp

Two hard rules for delivery (both brick the unit into a reboot loop if wrong):
- The **filename must start with `rmm-`** or `DslmSsp` rejects it (fault 9003) before it ever
  reaches the installer.
- The ACS `Download` RPC's **`FileSize` must equal the served byte count exactly**, or the
  femto downloads a truncated image, the apply fails, and it loops. Set `SDP_SIZE` in the ACS
  to `stat -c%s rmm-selfclean.sdp`.

## The hook (`selfclean_hook.sh`)

Runs as root, sourced first by `post_swdl_hook`. It has to (a) not leave a pending-upgrade
loop and (b) do the payload:

1. Kill the upgrade transaction so the next boot has nothing to commit:
   `rm cisco/UpgradeBeforeReboot cisco/VersionBeforeReboot`, `fw_setenv bank <good>`,
   `bootcount 0`, and flip `Upgrade.Current.InProgress` true->false in
   `cisco/tr069_{cur,bak}_cfg.xml.gz` (busybox `sed` needs uppercase `N` for the
   `{N;s|...|}` join, lowercase `n` silently no-ops).
2. Enable the stock sshd on `0.0.0.0:22` (`setnv_env.sh ENV_VERBOSE_CONSOLE_ENABLED TRUE`) and
   install `cwmp_rce_key.pub` root-owned, dir `700` (dropbear rejects group-writable).
3. `exit 0` - returns from the sourced script and skips `post_swdl_hook`'s destructive
   `delete_config` / `switching_bank` tail.

## Change the payload

Edit `selfclean_hook.sh`, rebuild with `sdp_pack.py`, update the ACS `SDP_SIZE` to the new
byte count, and re-arm (`rm acs/.selfclean_sent`). Keep the `rmm-` prefix and the trailing
`exit 0`.

## If it bricks

A bad package loops on the pending upgrade. Recover over JTAG (openocd on the PC302; the
config partition mtd4 `/var/ipaccess` @ flash `0x480000`, size `0x380000`) or reflash the
config partition. Because the package is hook-only (`0x5007` + `exit 0`) it never overwrites a
firmware bank, so `verifyimages` still passes and the dual-bank fallback stays intact.

## Files

- `sdp_pack.py` - the packer
- `selfclean_hook.sh` - the root hook (payload)
- `rmm-selfclean.sdp` - the built package
