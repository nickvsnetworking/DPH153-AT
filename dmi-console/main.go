package main

import (
	"bufio"
	"io"
	"net"
	"net/url"
	"os"
	"os/exec"
	"sort"
	"strconv"
	"strings"
	"time"
)

func run(name string, a ...string) string {
	c := exec.Command(name, a...)
	c.Env = append(os.Environ(), "LD_LIBRARY_PATH=/opt/ipaccess/lib")
	o, _ := c.CombinedOutput()
	return string(o)
}
func dmi(a ...string) string { return run("/opt/ipaccess/DMI/ipa-dmi", a...) }
func rf(p string) string     { b, _ := os.ReadFile(p); return string(b) }

func esc(s string) string {
	return strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;", `"`, "&#34;").Replace(s)
}

type attr struct{ n, v string }

func parse(s string) []attr {
	var a []attr
	for _, ln := range strings.Split(s, "\n") {
		ln = strings.TrimSpace(ln)
		i, j := strings.Index(ln, " ("), strings.Index(ln, ") = ")
		if i > 0 && j > i {
			a = append(a, attr{ln[:i], ln[j+4:]})
		}
	}
	sort.Slice(a, func(i, j int) bool { return a[i].n < a[j].n })
	return a
}
func sub(a []attr, ss ...string) []attr {
	var o []attr
	for _, x := range a {
		l := strings.ToLower(x.n)
		for _, s := range ss {
			if strings.Contains(l, s) {
				o = append(o, x)
				break
			}
		}
	}
	return o
}
func find(a []attr, name string) string {
	for _, x := range a {
		if x.n == name {
			return x.v
		}
	}
	return ""
}

type pair struct{ k, v string }

func kvtab(p []pair) string {
	var b strings.Builder
	b.WriteString("<table>")
	for _, x := range p {
		b.WriteString("<tr><td class=n>" + esc(x.k) + "</td><td class=v>" + esc(x.v) + "</td></tr>")
	}
	b.WriteString("</table>")
	return b.String()
}

func dur(sec int) string {
	d, h, m := sec/86400, sec/3600%24, sec/60%60
	if d > 0 {
		return strconv.Itoa(d) + "d " + strconv.Itoa(h) + "h " + strconv.Itoa(m) + "m"
	}
	return strconv.Itoa(h) + "h " + strconv.Itoa(m) + "m"
}
func meminfo(k string) string {
	for _, ln := range strings.Split(rf("/proc/meminfo"), "\n") {
		if strings.HasPrefix(ln, k) {
			return strings.TrimSpace(ln[len(k):])
		}
	}
	return "?"
}

func health() []pair {
	up := 0
	if f := strings.Fields(rf("/proc/uptime")); len(f) > 0 {
		if v, e := strconv.ParseFloat(f[0], 64); e == nil {
			up = int(v)
		}
	}
	load := strings.Fields(rf("/proc/loadavg"))
	l := ""
	if len(load) >= 3 {
		l = load[0] + " " + load[1] + " " + load[2]
	}
	return []pair{{"uptime", dur(up)}, {"load", l},
		{"mem free", meminfo("MemFree:") + " / " + meminfo("MemTotal:")}}
}

func ntpd() []pair {
	for _, ln := range strings.Split(run("/opt/ipaccess/bin/ntpq", "-pn"), "\n") {
		if strings.HasPrefix(ln, "*") {
			f := strings.Fields(ln)
			if len(f) >= 10 {
				return []pair{{"peer", f[0][1:]}, {"stratum", f[2]}, {"reach", f[6] + " (377=full)"},
					{"offset ms", f[8]}, {"jitter ms", f[9]}}
			}
		}
	}
	return []pair{{"ntpd", "NOT synced (no * peer) - oscillator undisciplined"}}
}

func gps() []pair {
	b, _ := os.ReadFile("/tmp/gps")
	fix := "NO FIX (buffer all-zero, 0 sats)"
	for _, x := range b {
		if x != 0 {
			fix = "fix data present in /tmp/gps"
			break
		}
	}
	agps := strings.TrimSpace(strings.SplitN(rf("/var/ipaccess/cisco/gps.cfg"), "\n", 2)[0])
	pow := "gpstest not running"
	if strings.Contains(run("ps"), "gpstest") {
		pow = "gpstest running"
	}
	return []pair{{"receiver", pow}, {"fix", fix}, {"agps source", agps}}
}

func cellUE(a []attr) []pair {
	get := func(n, d string) string {
		if v := find(a, n); v != "" {
			return v
		}
		return d
	}
	return []pair{
		{"rrmAdminState", get("rrmAdminState", "?")},
		{"rrmOperationalState", get("rrmOperationalState", "?")},
		{"csgAccessMode", get("csgAccessMode", "?")},
		{"hnbGwAddress", get("hnbGwAddress", "?")},
		{"hnbGw close cause", get("hnbGwConnectionCloseCause", "?")},
		{"active RABs", get("apActiveRabInfoList_001", get("activeRabs", "see All params"))},
	}
}

func ident() []pair {
	hw := run("strings", "/var/ipaccess/hw_description.dat")
	ser, mac := "?", "?"
	for _, ln := range strings.Split(hw, "\n") {
		if strings.HasPrefix(ln, "BCC810-") {
			ser = ln
		}
		if len(ln) == 17 && strings.Count(ln, ":") == 5 {
			mac = ln
		}
	}
	fw := strings.TrimSpace(run("uname", "-r"))
	return []pair{{"serial", ser}, {"mac", mac}, {"variant", "W3GFP-103 / 237B030"}, {"kernel", fw}}
}

func logtail(path string, n int) string {
	ls := strings.Split(strings.TrimRight(rf(path), "\n"), "\n")
	if len(ls) > n {
		ls = ls[len(ls)-n:]
	}
	return esc(strings.Join(ls, "\n"))
}

func rows(a []attr, ed bool) string {
	var b strings.Builder
	for _, x := range a {
		b.WriteString("<tr><td class=n>" + esc(x.n) + "</td><td class=v>" + esc(x.v) + "</td>")
		if ed {
			b.WriteString(`<td><form method=post action=/set style=margin:0><input type=hidden name=attr value="` +
				esc(x.n) + `"><input name=val size=8 placeholder=set><button>set</button></form></td>`)
		}
		b.WriteString("</tr>")
	}
	return b.String()
}

const css = `<style>body{font:13px monospace;margin:14px;background:#111;color:#ddd}h1{font-size:17px}h2{font-size:14px;color:#6cf;border-bottom:1px solid #333;margin-top:16px}table{border-collapse:collapse;width:100%}td{padding:2px 8px;border-bottom:1px solid #222;vertical-align:top}.n{color:#9c9;white-space:nowrap}.v{color:#fda}input,button{background:#222;color:#ddd;border:1px solid #444;padding:2px 6px}.al{color:#f66}#q{width:280px;margin:8px 0}a{color:#6cf}pre{background:#000;padding:8px;overflow:auto;max-height:220px;color:#9c9}.g{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:0 24px}</style>`

func page() string {
	a := parse(dmi("-c", "getobj"))
	al := sub(a, "alarm")
	return `<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">` + css +
		`<h1>DPH-153 DMI console <a href=/ style=float:right>&#8635; refresh</a></h1><div class=g>` +
		`<div><h2>System</h2>` + kvtab(health()) + `</div>` +
		`<div><h2>Identity</h2>` + kvtab(ident()) + `</div>` +
		`<div><h2>NTP / oscillator</h2>` + kvtab(ntpd()) + `</div>` +
		`<div><h2>GPS</h2>` + kvtab(gps()) + `</div>` +
		`<div><h2>Cell / UE</h2>` + kvtab(cellUE(a)) + `</div>` +
		`</div>` +
		`<h2 class=al>Active alarms (` + strconv.Itoa(len(al)) + `)</h2><table>` + rows(al, false) + `</table>` +
		`<h2>Network Listen (macro cells heard)</h2><table>` + rows(sub(a, "nwl"), false) + `</table>` +
		`<h2>Cell / RF / sync (settable)</h2><table>` + rows(sub(a, "adminstate", "operationalstate", "csgaccess", "hnbgw", "cpich", "uarfcn", "scrambl", "availab", "txpower", "crystal", "ntp"), true) + `</table>` +
		`<h2>All parameters (` + strconv.Itoa(len(a)) + `) &mdash; <input id=q placeholder=filter... oninput=f()></h2>` +
		`<table id=all>` + rows(a, true) + `</table>` +
		`<h2>Recent log (/var/log/messages)</h2><pre>` + logtail("/var/log/messages", 20) + `</pre>` +
		`<script>function f(){var q=document.getElementById('q').value.toLowerCase();document.querySelectorAll('#all tr').forEach(function(r){r.style.display=r.innerText.toLowerCase().indexOf(q)<0?'none':''})}</script>`
}

func form(body, k string) string {
	for _, kv := range strings.Split(body, "&") {
		if p := strings.SplitN(kv, "=", 2); len(p) == 2 && p[0] == k {
			v, _ := url.QueryUnescape(p[1])
			return v
		}
	}
	return ""
}
func doSet(body string) string {
	at, v := form(body, "attr"), form(body, "val")
	m := "no value"
	if at != "" && v != "" {
		m = esc(strings.TrimSpace(dmi("-c", "set "+at+"="+v)))
	}
	return css + "<p>set <b>" + esc(at) + "</b> = <b>" + esc(v) + "</b></p><pre>" + m + "</pre><a href=/>&larr; back</a>"
}

func handle(c net.Conn) {
	defer c.Close()
	c.SetDeadline(time.Now().Add(30 * time.Second))
	br := bufio.NewReader(c)
	rl, err := br.ReadString('\n')
	if err != nil {
		return
	}
	f := strings.Fields(rl)
	if len(f) < 2 {
		return
	}
	clen := 0
	for {
		h, e := br.ReadString('\n')
		if e != nil {
			return
		}
		h = strings.TrimRight(h, "\r\n")
		if h == "" {
			break
		}
		if strings.HasPrefix(strings.ToLower(h), "content-length:") {
			clen, _ = strconv.Atoi(strings.TrimSpace(h[15:]))
		}
	}
	body := make([]byte, clen)
	io.ReadFull(br, body)
	out := page()
	if f[1] == "/set" {
		out = doSet(string(body))
	}
	c.Write([]byte("HTTP/1.1 200 OK\r\nContent-Type:text/html\r\nConnection:close\r\nContent-Length:" + strconv.Itoa(len(out)) + "\r\n\r\n" + out))
}

func main() {
	p := os.Getenv("PORT")
	if p == "" {
		p = "8099"
	}
	ln, err := net.Listen("tcp", ":"+p)
	if err != nil {
		os.Exit(1)
	}
	for {
		if c, e := ln.Accept(); e == nil {
			go handle(c)
		}
	}
}
