"""Offline surface disagreement diagnostics. No network or external models.
Usage: diagnose_capture.py capture.json raw-directory output-directory
Writes per-view/patch evidence and a contact sheet; never edits the capture.
"""
import sys,json,time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import numpy as np,cv2
from elevation import Wall,depth_points,matrix
from quality import repeat_view_check
capture_path, raw, out = map(Path, sys.argv[1:4]); out.mkdir(parents=True, exist_ok=True)
cap=json.loads(capture_path.read_text()); walls=[Wall(w) for w in cap['walls']];floor=min(w.origin[1]-w.height/2 for w in walls)
cache=out/(capture_path.stem+'-near-surfaces.npz')
records=[json.loads(s) for s in (raw/'frames.jsonl').read_text().splitlines()];poses=np.array([matrix(f['cameraToWorld']) for f in records]);times=np.array([f['timestamp'] for f in records]);times-=times[0]
if not cache.exists():
 pts,ns,fs=depth_points(raw,with_frames=True,min_confidence=2); data={}
 for i,w in enumerate(walls):
  rel=pts-w.origin;a=rel@w.axis_x+w.width/2;d=rel@w.inward;h=pts[:,1]-floor
  use=(abs(d)<.3)&(a>0)&(a<w.width)&(h>.3)&(h<1.9)&(ns@w.inward>.9)
  data[f'w{i}']=np.column_stack([a[use],h[use],d[use],fs[use]])
 for name,lev,up in [('floor',floor,1),('ceiling',floor+cap.get('ceiling',{}).get('height',max(w.height for w in walls)),-1)]:
  use=(abs(pts[:,1]-lev)<.20)&(ns[:,1]*up>.95)
  data[name]=np.column_stack([pts[use][:,[0,2,1]],fs[use]])
 np.savez(cache,**data)
 print('cache ready',flush=True)
data=np.load(cache);report={'note':'Broad surface masks include clutter and openings; disagreements are not calibrated pose drift.', 'floorY':floor,'wallOrigins':[w.origin.tolist() for w in walls],'telemetry':{'duration':times[-1]}}
for name in data.files:
 v=data[name]; per=[]
 for f in np.unique(v[:,3]).astype(int):
  q=v[v[:,3]==f];xy=q[:,:2];z=q[:,2];A=np.c_[xy-np.median(xy,axis=0),np.ones(len(q))];beta=np.linalg.lstsq(A,z,rcond=None)[0]
  for _ in range(4):
   weights=np.sqrt(np.minimum(1,.01/np.maximum(abs(z-A@beta),1e-9)));beta=np.linalg.lstsq(A*weights[:,None],z*weights,rcond=None)[0]
  per.append({'frame':int(f),'time':times[f],'n':len(q),'med':float(np.median(z)),'p10':float(np.percentile(z,10)),'p90':float(np.percentile(z,90)),'slope':beta[:2].tolist(),'p90Residual':float(np.percentile(abs(z-A@beta),90))})
 report[name]={'framePlanes':per}
 if name[0]=='w':
  i=int(name[1:]);report[name]['oldCheck']=repeat_view_check(v[:,0],v[:,2],v[:,1],v[:,3],walls[i].width)
 print(name,'frames',len(per),'offset P10/50/90',np.percentile([x['med'] for x in per],[10,50,90]),'frame plane residual med',np.median([x['p90Residual'] for x in per]),flush=True)
 # Fine spatial matching: retain every observation; no depth-mode rejection.
 for cell in [.5,.1]:
  keys=np.c_[np.floor(v[:,:2]/cell).astype(int),v[:,3].astype(int)]
  _,inv=np.unique(keys,axis=0,return_inverse=True);order=np.argsort(inv);obs={}
  for _,start,count in zip(*np.unique(inv[order],return_index=True,return_counts=True)):
   if count<(20 if cell==.5 else 4):continue
   idx=order[start:start+count];k=tuple(keys[idx[0],:2]);f=int(keys[idx[0],2]);q=v[idx]
   obs.setdefault(k,[]).append((f,float(np.median(q[:,2])),float(np.ptp(q[:,2]))))
  pairs=[]
  for key,vals in obs.items():
   vals.sort(); t=np.array([a[0] for a in vals]);z=np.array([a[1] for a in vals]);s=t[-1]-t[0]
   if len(vals)<6 or s<6:continue
   a=z[t<=t[0]+s/3];b=z[t>=t[-1]-s/3]
   if len(a)<2 or len(b)<2:continue
   pairs.append({'cell':key,'shift':float(abs(np.median(b)-np.median(a))),'spread':float(np.percentile(z,90)-np.percentile(z,10)),'observations':vals})
  report[name][str(cell)]={'patches':len(pairs),'p90Shift':float(np.percentile([x['shift'] for x in pairs],90)) if pairs else None,'worst':sorted(pairs,key=lambda x:x['shift'],reverse=True)[:5]}
  print(name,cell,report[name][str(cell)]['patches'],report[name][str(cell)]['p90Shift'],flush=True)
(out/'raw-diagnostics.json').write_text(json.dumps(report,indent=2,default=lambda x:int(x)))
# Contact sheet, preserving source images.
ims=[]
for f in np.linspace(0, len(records)-1, 9).astype(int):
 im=cv2.imread(str(raw/'frames'/(records[f]['frame']+'.jpg')));im=cv2.resize(im,(480,360));cv2.putText(im,str(f)+' / '+str(round(times[f],1))+'s',(10,25),cv2.FONT_HERSHEY_SIMPLEX,.7,(0,0,255),2);ims.append(im)
cv2.imwrite(str(out/'frames-contact.jpg'),np.vstack([np.hstack(ims[i:i+3]) for i in range(0,9,3)]))
