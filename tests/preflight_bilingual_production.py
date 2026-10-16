"""READ-ONLY PRODUCTION PREFLIGHT. Does not publish, clone, merge, or create releases."""
import json,os,sys,hashlib,requests
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from dr_agenda_publisher_core import make_auth_session
API="https://firebasehosting.googleapis.com/v1beta1/"
PROJECT="dr-accesorios-rd"
SITES={
 "dr-accesorios-rd":[("/radar-alertas/index.html","landing/radar-alertas/index.html"),("/radar-alertas/agenda.html","landing/radar-alertas/agenda.html")],
 "dr-accesorios-rd-agenda":[("/agenda/index.html","agenda_site/agenda/index.html")]
}
def must(v,msg):
 if not v:raise RuntimeError("QA PRODUCTION BLOCKED: "+msg)
def get(session,path):
 response=session.get(API+path,timeout=35)
 must(response.status_code==200,"hosting API "+path+" HTTP "+str(response.status_code))
 return response.json()
def inventory(session,ver):
 allfiles={};token=None
 while True:
  params={"status":"ACTIVE","pageSize":1000}
  if token:params["pageToken"]=token
  response=session.get(API+ver+"/files",params=params,timeout=35)
  must(response.status_code==200,"cannot list version files")
  data=response.json()
  for r in data.get("files",[]):
   p=r.get("path");h=r.get("hash")
   must(p not in allfiles and isinstance(p,str) and isinstance(h,str),"invalid manifest")
   allfiles[p]=h
  token=data.get("nextPageToken")
  if not token:break
 return allfiles
def main():
 must(os.environ.get("GITHUB_ACTIONS")=="true" and os.environ.get("DR_PROD_CHECK")=="READ_ONLY","wrong execution mode")
 s=make_auth_session()
 for site,expected_files in SITES.items():
  data=get(s,f"projects/{PROJECT}/sites/{site}")
  must(data.get("defaultUrl")=="https://"+site+".web.app","site identity mismatch")
  live=get(s,"sites/"+site+"/channels/live")
  ver=((live.get("release") or {}).get("version") or {}).get("name")
  must(isinstance(ver,str) and "/sites/"+site+"/versions/" in "/"+ver,"live version missing")
  filehashes=inventory(s,ver)
  must(all(path in filehashes for path,_ in expected_files),"required path absent in live manifest")
  print("READ ONLY "+site+": active_files="+str(len(filehashes))+" | live_version_id="+ver.rsplit("/",1)[-1],flush=True)
  for path,src in expected_files:
   r=requests.get("https://"+site+".web.app"+path,timeout=30,headers={"Cache-Control":"no-cache"},allow_redirects=False)
   must(r.status_code==200 and 1000<len(r.content)<1000000,"public HTML unreadable: "+path)
   original=(ROOT/src).read_bytes()
   exact=(original==r.content)
   prefix=(r.content.find(b"DR_SITE_BILINGUAL_UI_QA_V1")>=0)
   print("PAGE "+site+path+": matches main source="+str(exact)+" | bilingual already deployed="+str(prefix)+" | len="+str(len(r.content)),flush=True)
  if site=="dr-accesorios-rd-agenda":
   r=requests.get("https://"+site+".web.app/agenda/events.json",timeout=30)
   must(r.status_code==200,"live events not readable")
   cat=r.json();must(cat.get("schemaVersion")==1 and isinstance(cat.get("events"),list),"catalog unexpected")
   print("LIVE EVENTS: count="+str(len(cat["events"]))+" | revision="+str(cat.get("revision"))+" | translated fields found="+str(sum(bool(e.get("titleEn") or e.get("title_en") or e.get("translations")) for e in cat["events"])),flush=True)
 print("READ ONLY COMPLETE. Production unchanged.",flush=True)
if __name__=="__main__":main()
