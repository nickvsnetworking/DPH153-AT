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

func dmi(a ...string) string {
	c := exec.Command("/opt/ipaccess/DMI/ipa-dmi", a...)
	c.Env = append(os.Environ(), "LD_LIBRARY_PATH=/opt/ipaccess/lib")
	o, _ := c.CombinedOutput()
	return string(o)
}

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

const css = `<style>body{font:13px monospace;margin:14px;background:#111;color:#ddd}h1{font-size:17px}h2{font-size:14px;color:#6cf;border-bottom:1px solid #333;margin-top:18px}table{border-collapse:collapse;width:100%}td{padding:2px 8px;border-bottom:1px solid #222;vertical-align:top}.n{color:#9c9;white-space:nowrap}.v{color:#fda}input,button{background:#222;color:#ddd;border:1px solid #444;padding:2px 6px}.al{color:#f66}#q{width:280px;margin:8px 0}a{color:#6cf}</style>`

func page() string {
	a := parse(dmi("-c", "getobj"))
	al := sub(a, "alarm")
	gp := sub(a, "atitude", "ongitude", "lockobtain", "satellite", "altitude", "gps", "gpio")
	ce := sub(a, "adminstate", "operationalstate", "csgaccess", "hnbgw", "cpich", "uarfcn", "scrambl", "availab", "txpower", "nwl", "crystal", "ntp")
	return `<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">` + css +
		`<h1>DPH-153 DMI console <a href=/ style=float:right>&#8635;</a></h1>` +
		`<h2 class=al>Active alarms (` + strconv.Itoa(len(al)) + `)</h2><table>` + rows(al, false) + `</table>` +
		`<h2>GPS / GPIO</h2><table>` + rows(gp, false) + `</table>` +
		`<h2>Cell / RF / sync</h2><table>` + rows(ce, true) + `</table>` +
		`<h2>All parameters (` + strconv.Itoa(len(a)) + `) &mdash; <input id=q placeholder=filter... oninput=f()></h2>` +
		`<table id=all>` + rows(a, true) + `</table>` +
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
	var out string
	if f[1] == "/set" {
		out = doSet(string(body))
	} else {
		out = page()
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
		c, e := ln.Accept()
		if e == nil {
			go handle(c)
		}
	}
}
