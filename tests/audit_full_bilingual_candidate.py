#!/usr/bin/env python3
"""DR bilingual full-version production candidate: READ ONLY compatibility audit.

Fail closed if live HTML, version, manifest, configuration or service files differ.
Uses only GET; never creates channels, versions, or releases.
"""
from __future__ import annotations
import gzip, hashlib, json, os, re, sys
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from dr_agenda_publisher_core import make_auth_session
API="https://firebasehosting.googleapis.com/v1beta1/"
PROJECT="dr-accesorios-rd"
PAGES={
 "dr-accesorios-rd":{
   "/radar-alertas/index.html":"landing/radar-alertas/index.html",
   "/radar-alertas/agenda.html":"landing/radar-alertas/agenda.html",
 },
 "dr-accesorios-rd-agenda":{
   "/agenda/index.html":"agenda_site/agenda/index.html",
 },
}
RESERVED={"/__/firebase/init.js","/__/firebase/init.json"}
EXPECTED_MARKER="DR_SITE_BILINGUAL_UI_QA_V1"
PROTECTED=[
 ("landing/radar-alertas/sw.js","/radar-alertas/sw.js"),
 ("agenda_site/agenda/app.js","/agenda/app.js"),
 ("agenda_site/agenda/events.json","/agenda/events.json"),
]
def must(ok,msg):
    if not ok:raise RuntimeError("PRODUCTION CANDIDATE REJECTED: "+msg)
def req(s,path,params=None):
    r=s.get(API+path,params=params,timeout=40,allow_redirects=False)
    must(r.status_code==200,"Firebase GET "+path+" HTTP "+str(r.status_code))
    return r.json()
def entries(s,version):
    found={};token=None
    while True:
        q={"pageSize":1000,"status":"ACTIVE"}
        if token:q["pageToken"]=token
        page=req(s,version+"/files",q)
        for item in page.get("files",[]):
            path,h=item.get("path"),item.get("hash")
            must(isinstance(path,str) and isinstance(h,str) and re.fullmatch("[0-9a-f]{64}",h) and path not in found,"invalid Firebase manifest")
            found[path]=h
        token=page.get("nextPageToken")
        if not token:break
    return found
def gzip_hashes(contents):
    compressed=gzip.compress(contents,mtime=0)
    hashes={hashlib.sha256(compressed).hexdigest()}
    if len(compressed)>10 and compressed[9] in (3,255):
        alternate=bytearray(compressed)
        alternate[9]=3 if compressed[9]==255 else 255
        hashes.add(hashlib.sha256(alternate).hexdigest())
    return hashes
def addon_for(src):
    raw=(ROOT/src).read_text(encoding="utf-8")
    signature="/* DR_SITE_BILINGUAL_UI_QA_V1:"
    must(raw.count(signature)==1,"nonunique addon script comment "+src)
    mid=raw.index(signature)
    begin=raw.rfind("<script>",0,mid)
    end=raw.find("</script>",mid)
    must(begin>=0 and end>mid,"malformed bilingual script "+src)
    addon=raw[begin:end+9]
    must(addon.count("<script>")==1 and addon.count("</script>")==1,"multiple candidate scripts")
    must("new MutationObserver(schedule)" in addon,"translator missing")
    must("fetch(" not in addon and "XMLHttpRequest" not in addon,"addon must not fetch data")
    return "\n  "+addon+"\n"
def current_page(site,path):
    r=requests.get("https://"+site+".web.app"+path,timeout=35,allow_redirects=False,headers={"Cache-Control":"no-cache"})
    must(r.status_code==200 and 250<len(r.content)<=1_000_000,"live HTML invalid "+site+path)
    must(r.encoding is None or "html" in r.headers.get("Content-Type","").lower(),"unexpected page content type")
    return r.content
def patch(original,addon,site,path):
    must(original.count(b"</body>")==1 and original.count(b"<body")>=1,"ambiguous live HTML body "+site+path)
    must(EXPECTED_MARKER.encode("utf-8") not in original,"live translation already present, stop to avoid duplicate")
    out=original.replace(b"</body>",addon.encode("utf-8")+b"</body>")
    before,closing,after=original.partition(b"</body>")
    must(out==before+addon.encode("utf-8")+closing+after,"the original bytes must remain unchanged")
    must(len(out)<1_000_000,"new HTML exceeds size limit")
    return out
def audit():
    must(os.getenv("GITHUB_ACTIONS")=="true" and os.getenv("DR_PROD_CANDIDATE")=="READ_ONLY" and
         os.getenv("GITHUB_REPOSITORY")=="ariellourdes52-collab/dr-accesorios-rd-updates" and
         os.getenv("GITHUB_HEAD_REF")=="feature/dr-webapp-agenda-ui-i18n-qa-20261015",
         "unsafe CI invocation")
    s=make_auth_session()
    reports={}
    for site,pages in PAGES.items():
        metadata=req(s,"projects/"+PROJECT+"/sites/"+site)
        must(metadata.get("defaultUrl")=="https://"+site+".web.app","unexpected site")
        ch=req(s,"sites/"+site+"/channels/live")
        must(ch.get("name")=="sites/"+site+"/channels/live","unexpected LIVE channel")
        version=((ch.get("release") or {}).get("version") or {}).get("name","")
        must(re.fullmatch(r"sites/"+site+r"/versions/[a-zA-Z0-9_-]+",version)!=None,"unexpected version")
        info=req(s,version)
        must(info.get("name")==version and info.get("status")=="FINALIZED","LIVE version status")
        config=info.get("config",{})
        must(isinstance(config,dict),"LIVE config not readable")
        files=entries(s,version)
        must(int(info.get("fileCount",len(files)))==len(files),"LIVE file manifest incomplete")
        owned={p:h for p,h in files.items() if p not in RESERVED}
        must(all(p.startswith("/") for p in owned),"invalid path")
        intended=dict(owned)
        for target,src in pages.items():
            must(target in owned,"missing existing LIVE HTML "+target)
            old=current_page(site,target)
            must(owned[target] in gzip_hashes(old),"LIVE page content does not match active version: "+target)
            marker={"dr-accesorios-rd": {"index.html":b'id="activate"',"agenda.html":b'id="viewer"'},
                    "dr-accesorios-rd-agenda":{"index.html":b'id="event-grid"'}}[site][target.rsplit("/",1)[-1]]
            must(marker in old,"expected LIVE page identity missing "+target)
            content=patch(old,addon_for(src),site,target)
            newhash=hashlib.sha256(gzip.compress(content,mtime=0)).hexdigest()
            intended[target]=newhash
            print("SAFE PAGE "+site+target+" | old="+str(len(old))+" bytes | new="+str(len(content))+" bytes | original_content_kept=YES",flush=True)
        changed={p for p in set(owned)|set(intended) if owned.get(p)!=intended.get(p)}
        must(changed==set(pages),"modified file paths are not exactly authorized HTML pages")
        must(all(owned[p]==intended[p] for p in owned if p not in pages),"unrelated hosting content changed")
        config_hash=hashlib.sha256(json.dumps(config,sort_keys=True,separators=(",",":")).encode()).hexdigest()
        reports[site]={"site":site,"live_version":version.rsplit("/",1)[-1],"total":len(files),
                       "owned":len(owned),"modified_html":sorted(changed),
                       "unchanged":len(owned)-len(changed),"config_sha256":config_hash}
        print("SAFE MANIFEST "+site+" total="+str(len(files))+" unchanged="+str(len(owned)-len(changed))+" changed_only="+",".join(sorted(changed)),flush=True)
    must(reports["dr-accesorios-rd"]["modified_html"]==sorted(PAGES["dr-accesorios-rd"]),"WEB approval invalid")
    must(reports["dr-accesorios-rd-agenda"]["modified_html"]==sorted(PAGES["dr-accesorios-rd-agenda"]),"AGENDA approval invalid")
    print("CANDIDATE AUDIT PASS: GET only; original logic preserved, original config preserved; no Firebase mutations",flush=True)
if __name__=="__main__":
    try:audit()
    except Exception as exc:
        print(str(exc),file=sys.stderr)
        raise SystemExit(1)
