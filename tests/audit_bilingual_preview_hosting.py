"""QA Hosting read-only preflight. Never creates versions, channels, or releases."""
import json
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from dr_agenda_publisher_core import make_auth_session
API="https://firebasehosting.googleapis.com/v1beta1/"
PROJECT="dr-accesorios-rd"
ALLOWED={"dr-accesorios-rd","dr-accesorios-rd-agenda"}

def read(session,path,allow_404=False):
    response=session.get(API+path,timeout=35,allow_redirects=False)
    if allow_404 and response.status_code==404:return None
    if response.status_code!=200:raise RuntimeError("QA audit HTTP "+str(response.status_code)+" at "+path)
    return response.json()

def main():
    if os.getenv('GITHUB_ACTIONS')!='true':raise RuntimeError("CI-only audit")
    if os.getenv('DR_PREVIEW_SITE_MODE')!='AUDIT_ONLY':raise RuntimeError("Audit-only guard")
    if os.getenv('FIREBASE_SERVICE_ACCOUNT','')=='':raise RuntimeError("Missing Firebase service account in Action")
    session=make_auth_session()
    for site in sorted(ALLOWED):
        item=read(session,f"projects/{PROJECT}/sites/{site}")
        expected="https://"+site+".web.app"
        if item.get('defaultUrl')!=expected or not str(item.get('name','')).endswith("/sites/"+site):
            raise RuntimeError("Unexpected site identity; block preview")
        live=read(session,"sites/"+site+"/channels/live",True)
        channels=read(session,"sites/"+site+"/channels")
        names=[r.get('name') for r in channels.get('channels',[])]
        if any(not str(x).startswith("sites/"+site+"/channels/") for x in names):
            raise RuntimeError("Unexpected channel identity")
        print("QA READ ONLY OK: site="+site+" | live_release="+str(bool(live and live.get('release')))+" | channels_count="+str(len(names)))
    print("READ ONLY completed: zero channel writes, zero versions, zero releases.")
if __name__=="__main__":main()
