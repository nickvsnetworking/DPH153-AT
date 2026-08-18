#!/usr/bin/env python3
"""
From-scratch ip.access .SDP packer for the DPH-153 (nano3G).
Produces a hook-only .SDP that the UNMODIFIED swdl_client accepts (all 3 CRCs pass,
PCB matches, no parser error) and that carries an sdp_hook shell script (type 0x5007).

Verified 2026-08-09: swdl_client -verbose 2 <url> -bank 1 -noswap →
  "Found sdp_hook script image" / "Image sdphook accepted" / "Successfully downloaded" /
  "The file ... was downloaded successfully."  (no Invalid CRC / no parser error state)

Format (big-endian throughout, CRCs = zlib.crc32):
  HEADER (fixed fields, item+0x78 starts at 4):
    ' SDP'(4)  totalheaderlength(4)=[4:8]  <unused>(4)=[8:12]
    totalfilelength(4)=[12:16] (= item+4 = total file length; snapshot fires at pos TFL-4)
    sdpversionid(120)=[16:136]  hwcompReserved(20)=[136:156]
    hwcomptablelength BE16=[156:158] (0 => skip hw table)
    itemindexlength  BE16       (= per-entry byte count; 1 entry = 138)
    <one index entry, 138 bytes>: id2 textdesc64 buildtime12 builddate14 userinit10
                                  versionid20 lengthbytes4 loadaddr4 execoffset4 offsettostart4
    sdpheadercrc(4) = zlib.crc32(everything before it)
  DATA:
    'IMAG'(4)  imgidType BE16(=0x5007 sdphook)  <script bytes>
    itemcrc(4) = zlib.crc32(script bytes only)   # item+0x70 re-inits at getdata entry
  TRAILER:
    filecrc(4) = zlib.crc32(all bytes before it)  # snapshot ~item+0x58 at pos TFL-4
Field notes: id, imgid type, and (crucially) offsettostart must be set so getdata matches
the data blob: offsettostart = 298 for the layout above (data 'IMAG' begins right after the
302-byte header at item+0x78 == 298+4). itemindexlength=138 makes exactly one index entry.
"""
import zlib, struct, sys
def b16(x): return struct.pack('>H', x & 0xffff)
def b32(x): return struct.pack('>I', x & 0xffffffff)
def crc(b): return zlib.crc32(b) & 0xffffffff

def build_sdp(script: bytes, item_type=0x5007):
    # placeholder totalfilelength; we know the size is fixed for a given script len
    # header up to sdpheadercrc field (298 bytes) + data + trailer
    def make(tfl):
        hb  = b' SDP'
        hb += b32(0)                 # totalheaderlength [4:8]
        hb += b32(0)                 # unused           [8:12]
        hb += b32(tfl)               # totalfilelength  [12:16]  (= item+4)
        hb += b'\x00'*120            # sdpversionid     [16:136]
        hb += b'\x00'*20             # hwcomp reserved  [136:156]
        hb += b16(0)                 # hwcomptablelength value = 0 (skip table)
        hb += b16(138)               # itemindexlength = per-entry bytes (1 entry)
        hb += b16(item_type)         # id
        hb += b'\x00'*64             # textdesc
        hb += b'\x00'*12             # buildtime
        hb += b'\x00'*14             # builddate
        hb += b'\x00'*10             # userinit
        hb += b'\x00'*20             # versionid
        hb += b32(len(script))       # lengthbytes
        hb += b32(0)                 # targetloadaddr
        hb += b32(0)                 # targetexecoffset
        hb += b32(298)               # offsettostart (data blob at item+0x78==302)
        h   = hb + b32(crc(hb))      # sdpheadercrc
        imag = b'IMAG' + b16(item_type)
        body = h + imag + script + b32(crc(script))   # + itemcrc
        whole= body + b32(crc(body))                  # + filecrc
        return whole
    n = len(make(0))                 # size is independent of the tfl value
    return make(n)

if __name__ == '__main__':
    script = open(sys.argv[1],'rb').read() if len(sys.argv)>1 else b'#!/bin/sh\ntouch /tmp/x\n'
    out = sys.argv[2] if len(sys.argv)>2 else 'out.sdp'
    data = build_sdp(script)
    open(out,'wb').write(data)
    print(f"wrote {out}: {len(data)} bytes, script {len(script)} bytes")
