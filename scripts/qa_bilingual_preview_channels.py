#!/usr/bin/env python3
"""PR #41 isolated Firebase Hosting preview channels; NEVER publishes live.
prepare: validate both sites and live snapshots, create TTL channels, build isolated
         static previews with network-sensitive actions disabled.
verify:  validate preview pages and unchanged live releases.
"""
from __future__ import annotations
import json, os, re, sys, stat
from pathlib import Path
from urllib.parse import urlparse
import requests
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"scripts"))
from dr_agenda_publisher_core import make_auth_session
API="https://firebasehosting.googleapis.com/v1beta1/"
PROJECT="dr-accesorios-rd"
WEB="dr-accesorios-rd"
AGENDA="dr-accesorios-rd-agenda"
CHANNEL="qa-bilingual-pr41"
TAG="dr-bilingual-qa41"
TEMP=Path(os.environ.get("RUNNER_TEMP","/tmp"))
SNAP=TEMP/"dr-bilingual-qa41-live.json"
CRED=TEMP/"dr-bilingual-qa41-credentials.json"
assert WEB != AGENDA and CHANNEL != "live"
def require(condition,message):
    if not condition: raise RuntimeError("Preview blocked: "+message)
def guard():
    require(os.getenv("GITHUB_ACTIONS")=="true","must run on GitHub Actions")
    require(os.getenv("GITHUB_REPOSITORY")=="ariellourdes52-collab/dr-accesorios-rd-updates","unexpected repository")
    require(os.getenv("DR_PREVIEW_MODE")=="QA41_PREVIEW_ONLY","preview-only guard absent")
    require(os.getenv("DR_QA_BRANCH")=="feature/dr-webapp-agenda-ui-i18n-qa-20261015","wrong branch")
    require(bool(os.environ.get("FIREBASE_SERVICE_ACCOUNT")),"Firebase credentials not configured")
    info=json.loads(os.environ["FIREBASE_SERVICE_ACCOUNT"])
    require(info.get("type")=="service_account" and info.get("project_id")==PROJECT,"wrong Firebase project key")
    CRED.write_text(json.dumps(info),encoding="utf-8")
    CRED.chmod(stat.S_IRUSR|stat.S_IWUSR)
    with open(os.environ["GITHUB_ENV"],"a",encoding="utf-8") as env:
        env.write("GOOGLE_APPLICATION_CREDENTIALS="+str(CRED)+"\n")
    return make_auth_session()
def api(session, method, path, *, data=None, params=None, notfound=False):
    require(method in {"GET","POST"},"mutating methods other than channel-create disabled")
    if method=="POST":
        require(re.fullmatch(r"sites/(?:dr-accesorios-rd|dr-accesorios-rd-agenda)/channels",path),"POST only channel creation")
        require(params=={"channelId":CHANNEL},"channel ID guard")
        require(data=={"ttl":"604800s","labels":{"managed_by":TAG}},"preview TTL/label guard")
    response=session.request(method,API+path,json=data,params=params,timeout=60,allow_redirects=False)
    if notfound and response.status_code==404:return None
    require(response.status_code in (200,201),"Firebase "+method+" "+path+" HTTP "+str(response.status_code))
    return response.json()
def channel_url(data,site):
    name="sites/"+site+"/channels/"+CHANNEL
    require(data.get("name")==name,"channel resource mismatch for "+site)
    url=data.get("url","")
    parsed=urlparse(url)
    require(parsed.scheme=="https" and parsed.hostname and parsed.hostname.startswith(site+"--")
            and parsed.hostname.endswith(".web.app") and not parsed.username and not parsed.password
            and parsed.path in ("","/") and not parsed.query and not parsed.fragment,
            "unexpected preview URL for "+site)
    return url.rstrip("/")
def audit_live(session):
    snapshots={}
    for site in (WEB,AGENDA):
        data=api(session,"GET",f"projects/{PROJECT}/sites/{site}")
        require(data.get("defaultUrl")=="https://"+site+".web.app","wrong Hosting site "+site)
        require(str(data.get("name","")).endswith("/sites/"+site),"wrong Hosting resource")
        live=api(session,"GET",f"sites/{site}/channels/live")
        require(live.get("name")=="sites/"+site+"/channels/live","wrong live channel name")
        name=((live.get("release") or {}).get("version") or {}).get("name")
        require(isinstance(name,str) and "/sites/"+site+"/versions/" in "/"+name,"live version not verified")
        snapshots[site]=name
    return snapshots
def channels(session):
    result={}
    for site in (AGENDA,WEB):
        path=f"sites/{site}/channels/{CHANNEL}"
        data=api(session,"GET",path,notfound=True)
        if data is None:
            data=api(session,"POST",f"sites/{site}/channels",
                     data={"ttl":"604800s","labels":{"managed_by":TAG}},
                     params={"channelId":CHANNEL})
        else:
            require((data.get("labels") or {}).get("managed_by")==TAG,"preview channel already belongs to someone else")
        result[site]=channel_url(data,site)
    return result
def exact_replace(text,old,new,what):
    require(text.count(old)==1,"ambiguous staging transform: "+what)
    return text.replace(old,new)
def meta_guard(html,origin,*,allow_fetch,frame_origin=None):
    src=" 'self' 'unsafe-inline'"
    parts=["default-src 'none'", "script-src"+src, "style-src"+src,
           "img-src 'self' data: https://dr-accesorios-rd-agenda.web.app",
           "font-src 'self' data:", "connect-src "+("'self'" if allow_fetch else "'none'"),
           "worker-src 'none'", "object-src 'none'", "form-action 'none'"]
    if frame_origin:parts.append("frame-src "+frame_origin)
    policy="; ".join(parts)
    header='<meta name="robots" content="noindex,nofollow,noarchive"><meta http-equiv="Content-Security-Policy" content="'+policy+'">'
    html=exact_replace(html,"</head>",header+"\n</head>","noindex policy")
    banner='<div id="dr-qa-preview-warning" role="note" style="padding:10px 14px;position:relative;z-index:5;background:#fff1f5;color:#792343;border:2px solid #bc1e59;text-align:center;font:bold 13px system-ui">ENTORNO DE PRUEBA · QA BILINGÜE · NO PRODUCCIÓN</div>'
    html=exact_replace(html,"<body>","<body>\n"+banner,"QA badge")
    return html
def read_local(rel,limit=1000000):
    path=(ROOT/rel).resolve()
    require(path.is_file() and path.is_relative_to(ROOT),"missing or unsafe local source "+rel)
    raw=path.read_bytes()
    require(0<len(raw)<=limit,"oversized asset "+rel)
    return raw
def write(files,target):
    folder=TEMP/("dr-pr41-stage-"+target)
    folder.mkdir(parents=True,exist_ok=True)
    for name,raw in files.items():
        require(name.startswith("/") and ".." not in Path(name).parts,"unsafe output asset path")
        file=folder/name.lstrip("/")
        file.parent.mkdir(parents=True,exist_ok=True)
        file.write_bytes(raw if isinstance(raw,bytes) else raw.encode("utf-8"))
    site= WEB if target=="web" else AGENDA
    config={"hosting":{"site":site,"public":str(folder),"ignore":["**/.*"],"headers":[{"source":"**","headers":[
        {"key":"Cache-Control","value":"no-store, max-age=0"},
        {"key":"X-Robots-Tag","value":"noindex, nofollow, noarchive"},
        {"key":"X-Content-Type-Options","value":"nosniff"}]}]}}
    (TEMP/("dr-pr41-"+target+".firebase.json")).write_text(json.dumps(config),encoding="utf-8")
    print("Prepared "+target+" isolated bundle: "+str(len(files))+" static paths, destination="+site+", channel="+CHANNEL,flush=True)
def source_web(name):
    return read_local("landing/radar-alertas/"+name)
def source_agenda(name):
    return read_local("agenda_site/"+name,2000000)
def prepare_assets(session,urls):
    web=urls[WEB]
    agenda=urls[AGENDA]
    weborigin=urlparse(web).scheme+"://"+urlparse(web).netloc
    agendaorigin=urlparse(agenda).scheme+"://"+urlparse(agenda).netloc
    parent_pair="const childOrigin='https://dr-accesorios-rd-agenda.web.app',parentOrigin='https://dr-accesorios-rd.web.app';"
    new_pair="const childOrigin='"+agendaorigin+"',parentOrigin='"+weborigin+"';"
    webfiles={}
    for name in ("index.html","agenda.html"):
        html=source_web(name).decode("utf-8")
        html=exact_replace(html,parent_pair,new_pair,"preview origins "+name)
        if name=="agenda.html":
            html=html.replace("https://dr-accesorios-rd-agenda.web.app/agenda/",agenda+"/agenda/")
            require(agenda+"/agenda/" in html,"iframe must use Agenda preview")
        html=meta_guard(html,weborigin,allow_fetch=False,frame_origin=agendaorigin)
        # Actions that would request real permissions, login or subscriptions are disabled only in preview copies.
        css='<style>#activate,#deactivate,#socialButton,#installButton{pointer-events:none!important;opacity:.48!important}</style>'
        html=exact_replace(html,"</head>",css+"</head>","preview sensitive actions disabled")
        webfiles["/radar-alertas/"+name]=html
    for name in ("icon-app-192.png","apple-touch-icon-180.png"):
        webfiles["/radar-alertas/"+name]=source_web(name)
    webfiles["/index.html"]='<meta name="robots" content="noindex"><a href="/radar-alertas/index.html">QA Web App</a>'
    webfiles["/robots.txt"]="User-agent: *\nDisallow: /\n"
    agenda_html=source_agenda("agenda/index.html").decode("utf-8")
    agenda_html=exact_replace(agenda_html,parent_pair,new_pair,"Agenda cross-frame origins")
    agenda_html=meta_guard(agenda_html,agendaorigin,allow_fetch=True)
    js=source_agenda("agenda/app.js").decode("utf-8")
    js=js.replace("https://dr-accesorios-rd-agenda.web.app/agenda/",agenda+"/agenda/")
    response=requests.get("https://dr-accesorios-rd-agenda.web.app/agenda/events.json",
                         headers={"Cache-Control":"no-cache","Accept":"application/json"},timeout=25)
    require(response.status_code==200 and 10<len(response.content)<650000,"cannot read public event catalog safely")
    data=response.json()
    require(data.get("schemaVersion")==1 and isinstance(data.get("revision"),int) and isinstance(data.get("events"),list),"invalid public catalog")
    data["events"]=data["events"][:250]
    catalog=(json.dumps(data,ensure_ascii=False,separators=(",",":"))+"\n").encode("utf-8")
    agendafiles={
      "/agenda/index.html":agenda_html,
      "/agenda/app.js":js,
      "/agenda/styles.css":source_agenda("agenda/styles.css"),
      "/agenda/logo-oficial.webp":source_agenda("agenda/logo-oficial.webp"),
      "/agenda/events.json":catalog,
      "/index.html":'<meta name="robots" content="noindex"><a href="/agenda/">QA DR Events</a>',
      "/robots.txt":"User-agent: *\nDisallow: /\n"
    }
    write(webfiles,"web")
    write(agendafiles,"agenda")
def save_snapshot(snap,urls):
    SNAP.write_text(json.dumps({"live":snap,"urls":urls}),encoding="utf-8")
def verify(session):
    require(SNAP.exists(),"no pre-publish live snapshot")
    snapshot=json.loads(SNAP.read_text())
    require(audit_live(session)==snapshot["live"],"one of the LIVE channels changed during QA")
    for site,path in ((WEB,"/radar-alertas/index.html"),(AGENDA,"/agenda/")):
        preview_url=snapshot["urls"][site]
        channel=api(session,"GET",f"sites/{site}/channels/{CHANNEL}")
        require(channel_url(channel,site)==preview_url and channel.get("release"),"preview release absent")
        resp=requests.get(preview_url+path,timeout=25,allow_redirects=False)
        require(resp.status_code==200,"preview HTTP "+str(resp.status_code)+" for "+site)
        require("DR_SITE_BILINGUAL_UI_QA_V1" in resp.text,"preview translation missing from "+site)
        require("noindex" in resp.text,"noindex missing from preview")
    print("QA PREVIEW RELEASE VERIFIED — no LIVE channel changes",flush=True)
    print("WEBAPP QA URL: "+snapshot["urls"][WEB]+"/radar-alertas/index.html",flush=True)
    print("AGENDA QA URL: "+snapshot["urls"][AGENDA]+"/agenda/",flush=True)
    print("Web Push, Google login and service workers BLOCKED on QA Web App by CSP; never test permission flows on preview.",flush=True)
def main():
    mode=sys.argv[1] if len(sys.argv)==2 else ""
    require(mode in ("prepare","verify"),"mode must be prepare or verify")
    session=guard()
    if mode=="prepare":
        live=audit_live(session)
        urls=channels(session)
        save_snapshot(live,urls)
        prepare_assets(session,urls)
        require(audit_live(session)==live,"live version changed during preparation")
        print("QA SAFE: channels prepared, LIVE untouched; publish only through hosting:channel:deploy.",flush=True)
    else:verify(session)
if __name__=="__main__":
    try:main()
    except Exception as exc:
        print("QA PREVIEW BLOCKED: "+str(exc),file=sys.stderr)
        raise SystemExit(1)
