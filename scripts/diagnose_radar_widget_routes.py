#!/usr/bin/env python3
import json, os, time, requests
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

SITE="sites/dr-accesorios-rd"
BASE="https://firebasehosting.googleapis.com/v1beta1/"
PUBLIC="https://dr-accesorios-rd.web.app"
secret=os.environ["FIREBASE_SERVICE_ACCOUNT"]
cred=service_account.Credentials.from_service_account_info(
    json.loads(secret),
    scopes=["https://www.googleapis.com/auth/firebase.hosting"],
)
s=AuthorizedSession(cred)

channel=s.get(BASE+SITE+"/channels/live",timeout=30)
channel.raise_for_status()
release=channel.json()["release"]
version_id=release["version"]["name"].rsplit("/",1)[1]
version_name=SITE+"/versions/"+version_id
ver=s.get(BASE+version_name,timeout=30)
ver.raise_for_status()
config=ver.json().get("config",{})
print("ACTIVE_RELEASE",release["name"])
print("ACTIVE_VERSION",version_name)
print("CONFIG",json.dumps(config,ensure_ascii=False))

paths=[
    "/radar-widget/",
    "/radar-widget/index.html",
    "/radar-widget",
]
for path in paths:
    for bust in (False,True):
        params={"diag":time.time_ns()} if bust else None
        r=requests.get(PUBLIC+path,params=params,headers={"Cache-Control":"no-cache","User-Agent":"Mozilla/5.0"},timeout=30,allow_redirects=False)
        print("CHECK",path,"BUST",bust,"STATUS",r.status_code,"LOC",r.headers.get("location"),"CACHE",r.headers.get("cache-control"),"AGE",r.headers.get("age"),"LEN",len(r.content),"MARKER",b"DR_RADAR_BLOGGER_WIDGET_V1" in r.content)
