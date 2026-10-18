"""Local SIFT/RGB-D revisit diagnostic, not a calibrated pose correction.
Usage: check_visual_revisits.py raw-directory output-directory
Uses every sixth saved frame, held-out image features, and no external APIs.
"""
import sys
import cv2,numpy as np,json,pathlib
p,out=map(pathlib.Path,sys.argv[1:3]);out.mkdir(parents=True,exist_ok=True);recs=[json.loads(s) for s in (p/'frames.jsonl').read_text().splitlines()];cv2.setRNGSeed(741);sf=cv2.SIFT_create(nfeatures=1400);bf=cv2.BFMatcher();frames=[]
for i in range(0,len(recs),6):
 f=recs[i];im=cv2.imread(str(p/'frames'/(f['frame']+'.jpg')),0);im=cv2.resize(im,(960,720));kp,des=sf.detectAndCompute(im,None)
 K=np.array(f['intrinsics']).reshape(3,3).T*.5;K[2,2]=1;pose=np.array(f['cameraToWorld']).reshape(4,4).T
 depth=np.fromfile(p/'depth'/(f['frame']+'.bin'),'<f4').reshape(192,256);conf=np.fromfile(p/'confidence'/(f['frame']+'.bin'),'u1').reshape(192,256)
 frames.append((i,np.array([k.pt for k in kp]),des,K,pose,depth,conf))
print('Features ready',flush=True);results=[]
for ai,a in enumerate(frames):
 for b in frames[ai+1:]:
  if a[2] is None or b[2] is None or len(a[2])<2 or len(b[2])<2:continue
  matches=[x for x,y in bf.knnMatch(a[2],b[2],k=2) if x.distance<.65*y.distance]
  if len(matches)<24:continue
  uv1=np.array([a[1][m.queryIdx] for m in matches]);uv2=np.array([b[1][m.trainIdx] for m in matches]);F,keep=cv2.findFundamentalMat(uv1,uv2,cv2.FM_RANSAC,1.,.999)
  if keep is None:continue
  use=keep.ravel().astype(bool);uv1=uv1[use];uv2=uv2[use]
  def world(uv,f):
   ij=np.clip((uv*np.array([256/960,192/720])).astype(int),[2,2],[253,189]);d=np.array([np.median(f[5][v-1:v+2,u-1:u+2]) for u,v in ij]);c=f[6][ij[:,1],ij[:,0]];rays=np.c_[uv,np.ones(len(uv))]@np.linalg.inv(f[3]).T;q=rays*d[:,None];q[:,1:]*=-1
   return q@f[4][:3,:3].T+f[4][:3,3],(c==2)&(d>.4)&(d<4)
  w1,k1=world(uv1,a);w2,k2=world(uv2,b);k=k1&k2;w1=w1[k];w2=w2[k];uv1=uv1[k];uv2=uv2[k]
  if len(w1)<24:continue
  train=np.arange(len(w1))%3!=0
  ok,rv,tv,inl=cv2.solvePnPRansac(w1[train],uv2[train],b[3],None,iterationsCount=300,reprojectionError=1.5,confidence=.999)
  if not ok or inl is None or len(inl)<14:continue
  cvpose=np.diag([1.,-1.,-1.,1.])@np.linalg.inv(b[4]);rvar=cv2.Rodrigues(cvpose[:3,:3])[0]
  pred=cv2.projectPoints(w1,rvar,cvpose[:3,3],b[3],None)[0].reshape(-1,2);fitted=cv2.projectPoints(w1,rv,tv,b[3],None)[0].reshape(-1,2)
  def median(q):return float(np.median(q))
  est=np.eye(4);est[:3,:3]=cv2.Rodrigues(rv)[0];est[:3,3]=tv.ravel();est=np.linalg.inv(est)@np.diag([1.,-1.,-1.,1.]);r=est[:3,:3].T@b[4][:3,:3]
  results.append({'a':a[0],'b':b[0],'matches':len(w1),'inliers':len(inl),'holdoutBeforePixels':median(np.linalg.norm(pred[~train]-uv2[~train],axis=1)),'holdoutAfterPixels':median(np.linalg.norm(fitted[~train]-uv2[~train],axis=1)),'pointDisagreementCm':median(np.linalg.norm(w1-w2,axis=1))*100,'poseTranslationCm':float(np.linalg.norm(est[:3,3]-b[4][:3,3])*100),'poseRotationDegrees':float(np.degrees(np.arccos(np.clip((np.trace(r)-1)/2,-1,1))))})
 print(a[0],len(results),flush=True)
(out/'visual-correspondences.json').write_text(json.dumps(results,indent=2));good=[x for x in results if x['b']-x['a']>60 and x['holdoutAfterPixels']<2];print('Good long revisits',json.dumps(sorted(good,key=lambda x:x['matches'],reverse=True)[:15]),flush=True)
