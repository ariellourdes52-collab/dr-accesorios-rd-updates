#!/usr/bin/env python3
"""One-time PR41 production promotion of fully audited Firebase Hosting versions.

Allowed updates: ONLY release prevalidated qa41-full-candidate versions to
the two existing LIVE channels. No files are built or uploaded. The version
and config are checked against the original LIVE versions immediately before
promotion. On failure, restore only our own releases (never overwrite
unrelated intervening releases).
"""
import os,sys,time,re,json
from pathlib import Path
import requests
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tests"))
import audit_full_bilingual_candidate as audit
from dr_agenda_publisher_core import make_auth_session
PROJECT="dr-accesorios-rd"
PREVIEW="qa41-full-candidate"
APP="dr-accesorios-rd"
AGENDA="dr-accesorios-rd-agenda"
ORDER=[AGENDA,APP]
PINNED_LIVE={
 APP:"df99e32934612440",
 AGENDA:"b9e87c1c4626000d",
}
STATUS_EN="Enable Web Push to receive DR Radar, earthquake alerts, new posts and DR Events notifications."
STATUS_ES="Falta activar Web Push para recibir Radar, sismos, nuevas publicaciones y avisos de DR Agenda."
def must(value,message):
    if not value: raise RuntimeError("PR41 SAFE RELEASE BLOCKED: "+message)
def guard():
    must(os.getenv("GITHUB_ACTIONS")=="true"
         and os.getenv("GITHUB_REPOSITORY")=="ariellourdes52-collab/dr-accesorios-rd-updates"
         and os.getenv("GITHUB_EVENT_NAME")=="pull_request"
         and os.getenv("GITHUB_HEAD_REF")=="feature/dr-webapp-agenda-ui-i18n-qa-20261015"
         and os.getenv("DR_PR41_RELEASE_MODE") in ("DRY_RUN","APPROVED_LIVE_PROMOTION"),
         "unexpected release context")
    key=json.loads(os.environ.get("FIREBASE_SERVICE_ACCOUNT","{}"))
    must(key.get("project_id")==PROJECT and key.get("type")=="service_account",
         "wrong Firebase credentials")
def api(session,method,path,*,params=None,payload=None):
    # Absolutely no versions, files or service-account operations can be mutated here.
    must(method=="GET" or
         (method=="POST"
          and any(path==f"sites/{site}/channels/live/releases" for site in ORDER)
          and params and set(params)=={"versionName"}
          and re.fullmatch(r"sites/(?:dr-accesorios-rd|dr-accesorios-rd-agenda)/versions/[A-Za-z0-9_-]+",
                           params["versionName"])),
         "prohibited Hosting API action "+method+" "+path)
    response=session.request(method,audit.API+path,params=params,
                             json=payload if method=="POST" else None,
                             timeout=60,allow_redirects=False)
    must(response.status_code in (200,201),
         method+" "+path+" HTTP "+str(response.status_code)+" "+response.text[:120])
    return response.json()
def current_live(session,site):
    channel=api(session,"GET",f"sites/{site}/channels/live")
    must(channel.get("name")==f"sites/{site}/channels/live","LIVE channel identity mismatch")
    version=((channel.get("release") or {}).get("version") or {}).get("name")
    must(isinstance(version,str) and version.startswith(f"sites/{site}/versions/"),
         "LIVE version unknown")
    return version
def fetch_version(session,version,site):
    must(re.fullmatch(rf"sites/{site}/versions/[A-Za-z0-9_-]+",version) is not None,
         "wrong candidate version site")
    info=api(session,"GET",version)
    must(info.get("name")==version and info.get("status")=="FINALIZED",
         "candidate not finalized")
    files=audit.entries(session,version)
    must(int(info.get("fileCount",len(files)))==len(files),
         "incomplete version manifest")
    return info,files
def validate(session,site):
    project=api(session,"GET",f"projects/{PROJECT}/sites/{site}")
    must(project.get("defaultUrl")=="https://"+site+".web.app","site identity mismatch")
    original=current_live(session,site)
    must(original==f"sites/{site}/versions/{PINNED_LIVE[site]}",
         "LIVE changed since user-approved candidate; abort without deployment")
    original_info,original_files=fetch_version(session,original,site)
    candidate_channel=api(session,"GET",f"sites/{site}/channels/{PREVIEW}")
    must(candidate_channel.get("name")==f"sites/{site}/channels/{PREVIEW}"
         and candidate_channel.get("labels",{}).get("managed_by")=="dr-pr41-full-bilingual",
         "unexpected preview channel identity")
    preview_version=((candidate_channel.get("release") or {}).get("version") or {}).get("name")
    must(preview_version and preview_version !=original,"missing or unchanged preview version")
    candidate_info,candidate_files=fetch_version(session,preview_version,site)
    must(candidate_info.get("config",{})==original_info.get("config",{}),
         "candidate changes LIVE Hosting config")
    nonreserved=lambda files:{p:h for p,h in files.items() if p not in audit.RESERVED}
    existing=nonreserved(original_files)
    new=nonreserved(candidate_files)
    must(set(new)==set(existing),
         "candidate would add or remove LIVE files")
    changed={p for p in new if new[p]!=existing[p]}
    authorized=set(audit.PAGES[site])
    must(changed==authorized,
         "candidate changes paths outside exactly authorized HTML pages "+str(changed))
    must(all(new[p]==existing[p] for p in existing if p not in changed),
         "candidate modifies unrelated file hashes")
    # Direct GETs of approved candidate must be valid, including the final requested status fix.
    url=candidate_channel.get("url","")
    must(url.startswith("https://"+site+"--") and url.endswith(".web.app"),
         "unexpected candidate URL")
    for path in authorized:
        response=requests.get(url+path,timeout=35,allow_redirects=False)
        must(response.status_code==200 and "DR_SITE_BILINGUAL_UI_QA_V1" in response.text,
             "approved preview not served "+site+path)
        if site==APP and path=="/radar-alertas/index.html":
            must(STATUS_EN in response.text and STATUS_ES in response.text,
                 "last approved status translation missing")
    if site==AGENDA:
        # Same event catalog hash, revision and service code: no changes at all.
        must(existing["/agenda/events.json"]==new["/agenda/events.json"],
             "live events catalog hash changed")
        must(existing["/agenda/app.js"]==new["/agenda/app.js"],
             "Agenda engine hash changed")
    else:
        must(existing["/radar-alertas/sw.js"]==new["/radar-alertas/sw.js"],
             "Web Push worker hash changed")
    print("PREFLIGHT OK "+site+" | live="+original.rsplit("/",1)[-1]+
          " | candidate="+preview_version.rsplit("/",1)[-1]+
          " | preserved_assets="+str(len(new)-len(changed))+
          " | changed_only="+",".join(sorted(changed)),flush=True)
    return original,preview_version
def release(session,site,version):
    # Only one low-level write is permitted: create a channel LIVE release of a
    # prevalidated existing FINALIZED Hosting version; no file upload or config edits.
    r=api(session,"POST",f"sites/{site}/channels/live/releases",
          params={"versionName":version},
          payload={"message":"Approved PR41 bilingual UI-only release; original application logic preserved"})
    must((r.get("version") or {}).get("name")==version,
         "release response version mismatch for "+site)
def verify(session,site,expected):
    for attempt in range(15):
        current=current_live(session,site)
        if current==expected:break
        if attempt<14:time.sleep(3)
    must(current==expected,"LIVE API version not updated "+site)
    if site==APP:
        route="/radar-alertas/index.html"
        require_text=STATUS_EN
    else:
        route="/agenda/index.html"
        require_text="DR_SITE_BILINGUAL_UI_QA_V1"
    for attempt in range(15):
        resp=requests.get("https://"+site+".web.app"+route,timeout=35,
                          headers={"Cache-Control":"no-cache"},allow_redirects=False)
        if resp.status_code==200 and require_text in resp.text:break
        if attempt<14:time.sleep(3)
    must(resp.status_code==200 and require_text in resp.text,
         "PUBLIC HTML did not reflect approved candidate in time "+site)
    print("PRODUCTION VERIFIED "+site+" version="+expected.rsplit("/",1)[-1],flush=True)
def main():
    guard()
    mode=os.environ["DR_PR41_RELEASE_MODE"]
    session=make_auth_session()
    plans={site:validate(session,site) for site in ORDER}
    if mode=="DRY_RUN":
        print("SAFE DRY RUN PASS. ZERO WRITES. LIVE NOT CHANGED.",flush=True)
        return
    committed=[]
    try:
        for site in ORDER:
            old,new=plans[site]
            must(current_live(session,site)==old,
                 "LIVE was updated by another publisher; abort")
            release(session,site,new)
            committed.append(site)
            verify(session,site,new)
        print("PR41 BILINGUAL LIVE RELEASE SUCCESS; original app logic, files and configs preserved.",flush=True)
    except Exception as exc:
        print("RELEASE ERROR: "+str(exc)+"; attempting guarded rollback",file=sys.stderr,flush=True)
        for site in reversed(committed):
            old,new=plans[site]
            try:
                if current_live(session,site)==new:
                    release(session,site,old)
                    verify(session,site,old)
                    print("GUARDED ROLLBACK OK "+site,flush=True)
                else:
                    print("ROLLBACK SKIPPED: "+site+" updated externally; would overwrite another release",
                          file=sys.stderr,flush=True)
            except Exception as rollback_error:
                print("ROLLBACK FAILED "+site+": "+str(rollback_error),file=sys.stderr,flush=True)
        raise
if __name__=="__main__":
    try:main()
    except Exception as error:
        print(str(error),file=sys.stderr)
        raise SystemExit(1)
