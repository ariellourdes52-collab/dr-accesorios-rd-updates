#!/usr/bin/env python3
"""DR bilingual PR41 COMPLETE preview only, preserving full LIVE manifest/config.

Only writes sites/{allowed}/versions and preview channel qa41-full-candidate.
Never writes, releases or patches channels/live, main, events.json, service worker,
or application logic. Failure leaves LIVE unchanged.
"""
from __future__ import annotations
import gzip,hashlib,json,os,re,sys,time
from urllib.parse import urlparse
from pathlib import Path
import requests
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tests"))
import audit_full_bilingual_candidate as audit
from dr_agenda_publisher_core import make_auth_session
BASE=audit.API
PROJECT=audit.PROJECT
PAGES=audit.PAGES
RESERVED=audit.RESERVED
CHANNEL="qa41-full-candidate"
LABEL="dr-pr41-full-bilingual"
TTL="604800s"
def must(ok,message):
    audit.must(ok,message)
def guard():
    must(os.getenv("GITHUB_ACTIONS")=="true"
         and os.getenv("GITHUB_EVENT_NAME")=="pull_request"
         and os.getenv("GITHUB_HEAD_REF")=="feature/dr-webapp-agenda-ui-i18n-qa-20261015"
         and os.getenv("GITHUB_REPOSITORY")=="ariellourdes52-collab/dr-accesorios-rd-updates"
         and os.getenv("DR_FULL_QA_PREVIEW")=="PREVIEW_ONLY"
         and os.getenv("FIREBASE_SERVICE_ACCOUNT",""),"safe preview env guard missing")
    metadata=json.loads(os.environ["FIREBASE_SERVICE_ACCOUNT"])
    must(metadata.get("type")=="service_account" and metadata.get("project_id")==PROJECT,"wrong Firebase credentials")
def api(session,method,path,*,params=None,payload=None,notfound=False):
    must(method in ("GET","POST","PATCH"),"forbidden HTTP method")
    # Preview releases, never live: the only allowed release target is qa41-full-candidate.
    if method=="POST":
        allowed_version=any(path==f"sites/{site}/versions" for site in PAGES)
        allowed_files=any(re.fullmatch(rf"sites/{site}/versions/[a-zA-Z0-9_-]+:populateFiles",path) for site in PAGES)
        allowed_channel=any(path==f"sites/{site}/channels" and params=={"channelId":CHANNEL} for site in PAGES)
        allowed_release=any(path==f"sites/{site}/channels/{CHANNEL}/releases" for site in PAGES)
        must(allowed_version or allowed_files or allowed_channel or allowed_release,"unsafe POST path "+path)
    if method=="PATCH":
        must(any(re.fullmatch(rf"sites/{site}/versions/[a-zA-Z0-9_-]+",path) for site in PAGES)
             and params=={"updateMask":"status"} and payload=={"status":"FINALIZED"},
             "unsafe PATCH blocked")
    response=session.request(method,BASE+path,params=params,json=payload if method!="GET" else None,
                             timeout=90,allow_redirects=False)
    if notfound and response.status_code==404:return None
    must(response.status_code in (200,201),"Hosting "+method+" "+path+" HTTP "+str(response.status_code)+" "+response.text[:180])
    return response.json() if response.content else {}
def active_info(session,site):
    identity=audit.req(session,f"projects/{PROJECT}/sites/{site}")
    must(identity.get("defaultUrl")=="https://"+site+".web.app","site identity mismatch")
    live=audit.req(session,"sites/"+site+"/channels/live")
    must(live.get("name")=="sites/"+site+"/channels/live","LIVE identity mismatch")
    version=((live.get("release") or {}).get("version") or {}).get("name")
    must(isinstance(version,str) and re.fullmatch(rf"sites/{site}/versions/[a-zA-Z0-9_-]+",version),"LIVE version missing")
    meta=audit.req(session,version)
    must(meta.get("name")==version and meta.get("status")=="FINALIZED","LIVE metadata mismatch")
    manifest=audit.entries(session,version)
    must(int(meta.get("fileCount",len(manifest)))==len(manifest),"LIVE manifest incomplete")
    config=meta.get("config",{})
    must(isinstance(config,dict),"LIVE config invalid")
    return version,config,manifest
def get_channel(session,site):
    path=f"sites/{site}/channels/{CHANNEL}"
    data=api(session,"GET",path,notfound=True)
    if data is None:
        data=api(session,"POST",f"sites/{site}/channels",params={"channelId":CHANNEL},
                 payload={"ttl":TTL,"labels":{"managed_by":LABEL}})
    else:must((data.get("labels") or {}).get("managed_by")==LABEL,"preview channel belongs to another deployment")
    must(data.get("name")==path,"wrong preview channel")
    u=data.get("url","")
    loc=urlparse(u)
    must(loc.scheme=="https" and loc.hostname and loc.hostname.startswith(site+"--")
         and loc.hostname.endswith(".web.app") and loc.path in ("","/")
         and not loc.query and not loc.fragment and not loc.username and not loc.password,
         "preview URL failed strict validation")
    return u.rstrip("/")
def manifest_status(session,version,status):
    must(status in ("ACTIVE","EXPECTED"),"wrong status")
    entries={};token=None
    while True:
        q={"pageSize":1000,"status":status}
        if token:q["pageToken"]=token
        page=api(session,"GET",version+"/files",params=q)
        for row in page.get("files",[]):
            p,h=row.get("path"),row.get("hash")
            must(isinstance(p,str) and isinstance(h,str) and p not in entries,"invalid candidate manifest")
            entries[p]=h
        token=page.get("nextPageToken")
        if not token:break
    return entries
def prepare(site,config,live):
    owned={p:h for p,h in live.items() if p not in RESERVED}
    wanted=dict(owned)
    uploads={}
    for path,filename in PAGES[site].items():
        old=audit.current_page(site,path)
        must(owned.get(path) in audit.gzip_hashes(old),"LIVE public HTML hash mismatch "+path)
        raw=audit.patch(old,audit.addon_for(filename),site,path)
        content=gzip.compress(raw,mtime=0)
        digest=hashlib.sha256(content).hexdigest()
        wanted[path]=digest
        uploads[digest]=content
    changed={p for p in owned if owned[p]!=wanted[p]}
    must(changed==set(PAGES[site]),"unapproved HTML path change")
    must(set(wanted)==set(owned),"candidate changed LIVE path inventory")
    must(all(wanted[k]==v for k,v in owned.items() if k not in changed),"unrelated file mismatch")
    return wanted,uploads
def build_version(session,site,config,wanted,uploads):
    created=api(session,"POST",f"sites/{site}/versions",payload={"config":config})
    name=created.get("name","")
    must(bool(re.fullmatch(rf"sites/{site}/versions/[a-zA-Z0-9_-]+",name))
         and created.get("status")=="CREATED","created wrong Hosting version")
    response=api(session,"POST",name+":populateFiles",payload={"files":wanted})
    required=set(response.get("uploadRequiredHashes",[]))
    must(required<=set(uploads),"Firebase asked to re-upload immutable LIVE files: "+str(len(required-set(uploads))))
    uri=response.get("uploadUrl","")
    u=urlparse(uri)
    must(u.scheme=="https" and u.hostname=="upload-firebasehosting.googleapis.com"
         and "/sites/"+site+"/versions/"+name.rsplit("/",1)[-1]+"/files" in u.path,
         "unsafe Firebase upload URL")
    for digest in required:
        result=session.post(uri.rstrip("/")+"/"+digest,data=uploads[digest],
                            headers={"Content-Type":"application/octet-stream"},timeout=90)
        must(result.status_code==200,"Firebase rejected authorized changed HTML upload")
    for attempt in range(12):
        actual=manifest_status(session,name,"ACTIVE")
        pending=manifest_status(session,name,"EXPECTED")
        missing=set(wanted)-set(actual)
        invalid={p for p in set(wanted)&set(actual) if wanted[p]!=actual[p]}
        extra=set(actual)-set(wanted)
        must(not invalid and extra<=RESERVED,"candidate altered unexpected hashes or file paths")
        must(not (set(actual)&set(pending)),"candidate has duplicate pending/active paths")
        if not missing and not pending:break
        if attempt==11:raise RuntimeError("candidate manifest remains incomplete")
        time.sleep(3)
    finalized=api(session,"PATCH",name,params={"updateMask":"status"},payload={"status":"FINALIZED"})
    must(finalized.get("status")=="FINALIZED","candidate finalization failed")
    persisted=api(session,"GET",name)
    must(persisted.get("config",{})==config,"candidate Hosting configuration differs from LIVE")
    print("CANDIDATE VERSION OK: "+site+" total_unchanged_paths="+str(len(wanted)-len(uploads))
          +" authorized_updated_html="+str(len(uploads)),flush=True)
    return name
def release(session,site,channel_url,name,version_snapshot):
    current,_,_=active_info(session,site)
    must(current==version_snapshot,"LIVE changed; do not publish even to preview")
    data=api(session,"POST",f"sites/{site}/channels/{CHANNEL}/releases",
             params={"versionName":name},payload={"message":"PR41 full LIVE-preserving bilingual preview (not production)"})
    must(data.get("version",{}).get("name")==name,"preview release returned wrong version")
    for path in PAGES[site]:
        response=None
        for i in range(12):
            response=requests.get(channel_url+path,timeout=25,allow_redirects=False)
            if response.status_code==200 and audit.EXPECTED_MARKER in response.text:break
            if response.status_code not in (404,503,200):break
            if i<11:time.sleep(4)
        must(response is not None and response.status_code==200
             and audit.EXPECTED_MARKER in response.text,"candidate preview URL invalid "+site+path)
    return True
def main():
    guard()
    session=make_auth_session()
    snapshots={}
    for site in PAGES:
        version,config,old=active_info(session,site)
        snapshots[site]=version
        wanted,uploads=prepare(site,config,old)
        url=get_channel(session,site)
        candidate=build_version(session,site,config,wanted,uploads)
        release(session,site,url,candidate,version)
        print("FULL QA CANDIDATE LINK: "+url+next(iter(PAGES[site])),flush=True)
        print("FULL MANIFEST VERIFIED: "+site+" kept "+str(len(old))+" original manifest entries, changed only "+str(len(uploads))+" HTMLs",flush=True)
    for site,snapshot in snapshots.items():
        assert active_info(session,site)[0]==snapshot,"LIVE changed after candidate creation"
    print("FULL CANDIDATES APPROVED: Both LIVE versions unchanged. NO PRODUCTION RELEASE.",flush=True)
if __name__=="__main__":
    try:main()
    except Exception as exc:
        print("FULL QA BLOCKED: "+str(exc),file=sys.stderr)
        raise SystemExit(1)
