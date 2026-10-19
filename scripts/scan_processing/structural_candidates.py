"""Conservative, reviewable structural proposals from repeated depth surfaces.

No model can guarantee recall in occluded space. These proposals deliberately
retain partial coverage and never claim surveyed accuracy or load-bearing use.
"""
import hashlib
import cv2
import numpy as np


VERSION='depth-structure-v1'


def element(kind,profile,bottom,top,frames,capture_id=None,observed=(),inferred=()):
    profile=np.asarray(profile,float)
    center=profile.mean(axis=0)
    local=profile-center
    identity=f'{capture_id}:{kind}:{np.round(center,3).tolist()}:{bottom:.3f}:{top:.3f}'
    value={'id':'struct-'+hashlib.sha256(identity.encode()).hexdigest()[:20], 'kind':kind,
           'origin':{'x':float(center[0]),'y':float(bottom),'z':float(center[1])},'rotation':[0,0,0,1],
           'geometry':{'type':'extrusion','profile':[{'x':float(x),'z':float(z)} for x,z in local], 'length':float(top-bottom)},
           'evidence':'estimated','review':'candidate',
           'coverage':{'status':'partial','observedFaces':list(observed),'inferredFaces':list(inferred)},
           'provenance':{'processingVersion':VERSION,'frameIds':sorted(set(map(int,frames)))},
           'connections':[], 'quality':{'absoluteAccuracyVerified':False,'status':'unverified'}}
    if capture_id: value['provenance']['captureId']=capture_id
    return value


def components(cells):
    """Sparse connected components without allocating an entire room image."""
    unseen=set(map(tuple,cells))
    while unseen:
        first=min(unseen);unseen.remove(first);stack=[first];group=[]
        while stack:
            x,z=stack.pop();group.append((x,z))
            for dx,dz in [(1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)]:
                nxt=(x+dx,z+dz)
                if nxt in unseen:unseen.remove(nxt);stack.append(nxt)
        yield np.asarray(group)


def columns(points,normals,frames,walls=(),capture_id=None,cell=.06):
    """Track vertical cross-sections across multiple height bands.

    Wall planes are excluded, not whole wall-adjacent volumes. Ambiguous/open
    profiles remain unknown-structural proposals with inferred side labels.
    Final dimensional fitting and furniture classification need real scans.
    """
    points,normals,frames=np.asarray(points),np.asarray(normals),np.asarray(frames)
    if not len(points): return []
    keep=(abs(normals[:,1])<.35)&(points[:,1]>.15)
    for wall in walls:
        a=np.asarray([wall['start']['x'],wall['start']['z']]);b=np.asarray([wall['end']['x'],wall['end']['z']])
        axis=b-a;length=np.linalg.norm(axis)
        if length<1e-6:continue
        axis/=length;rel=points[:,[0,2]]-a
        along=rel@axis;distance=abs(rel@np.array([-axis[1],axis[0]]))
        keep &= ~((distance<.045)&(along>-.05)&(along<length+.05))
    p,n,f=points[keep],normals[keep],frames[keep]
    if not len(p):return []
    cells=np.floor(p[:,[0,2]]/cell).astype(int)
    unique,inverse=np.unique(cells,axis=0,return_inverse=True)
    # Persistence requires both height span and independent frames; repeated
    # pixels in one frame cannot establish a column.
    persistent=[]
    order=np.argsort(inverse,kind='stable')
    splits=np.flatnonzero(np.diff(inverse[order]))+1
    for i,use in enumerate(np.split(order,splits)):
        c=unique[i]
        if len(np.unique(np.floor(p[use,1]/.25)))>=4 and len(np.unique(f[use]))>=2:
            persistent.append(c)
    if not persistent:return []
    found=[]
    for group in components(persistent):
        if len(group)<6:continue
        cell_index={tuple(c):i for i,c in enumerate(unique)}
        use=np.isin(inverse,[cell_index[tuple(c)] for c in group])
        q=p[use];source_frames=f[use]
        bottom,top=np.percentile(q[:,1],[1,99])
        if top-bottom<.8:continue
        xz=q[:,[0,2]].astype(np.float32)
        rect=cv2.minAreaRect(xz);width,depth=rect[1]
        if min(width,depth)<.1 or max(width,depth)>1.8:continue
        profile=cv2.boxPoints(rect).astype(float)
        # Require observed evidence near every rectangular side to label a
        # rectangular column. Otherwise retain ambiguity, not fabricated faces.
        faces=[]
        for i in range(4):
            a,b=profile[i],profile[(i+1)%4];axis=b-a;axis/=np.linalg.norm(axis)
            distance=abs((xz-a)@np.array([-axis[1],axis[0]]))
            side=distance<.025
            if side.sum()>=12 and len(np.unique(source_frames[side]))>=2:faces.append(f'side-{i}')
        kind='column' if len(faces)==4 else 'unknown-structural'
        value=element(kind,profile,bottom,top,source_frames,capture_id,faces,
                      ['bottom','top']+[f'side-{i}' for i in range(4) if f'side-{i}' not in faces])
        # Fit a circle only with broad angular evidence; a partial arc must not
        # invent an unseen radius. Compare residual against rectangular sides.
        xy=xz.astype(float);design=np.column_stack([2*xy,np.ones(len(xy))])
        solution=np.linalg.lstsq(design,np.sum(xy**2,axis=1),rcond=None)[0]
        center=solution[:2];radius2=solution[2]+center@center
        if radius2>0:
            radius=np.sqrt(radius2);residual=abs(np.linalg.norm(xy-center,axis=1)-radius)
            angular=np.floor((np.arctan2(xy[:,1]-center[1],xy[:,0]-center[0])+np.pi)/(2*np.pi)*24).astype(int)%24
            if .06<radius<.9 and np.percentile(residual,90)<.015 and len(np.unique(angular))>=21:
                value['kind']='column';value['origin'].update(x=float(center[0]),z=float(center[1]))
                value['geometry']={'type':'cylinder','radius':float(radius),'length':float(top-bottom)}
                value['coverage']={'status':'partial','observedFaces':['lateral'],'inferredFaces':['bottom','top']}
        found.append(value)
    return found


def beams(points,normals,frames,ceiling_height,capture_id=None,cell=.06):
    """Separate connected observed underside footprints and retain concavity.

    This horizontal-underside detector does not fit sloped beams. Regions with
    holes are withheld until the canonical geometry can represent those holes.
    """
    points,normals,frames=np.asarray(points),np.asarray(normals),np.asarray(frames)
    if not len(points) or ceiling_height is None:return []
    keep=(normals[:,1]<-.85)&(points[:,1]>1.6)&(points[:,1]<ceiling_height-.06)
    p,f=points[keep],frames[keep]
    if not len(p):return []
    # Split measured levels at actual gaps, rather than arbitrary bin borders.
    order=np.argsort(p[:,1]);splits=np.flatnonzero(np.diff(p[order,1])>.05)+1
    found=[]
    for level_indices in np.split(order,splits):
        q,source=p[level_indices],f[level_indices]
        if np.ptp(q[:,1])>.08:continue
        cells=np.floor(q[:,[0,2]]/cell).astype(int)
        unique,inverse=np.unique(cells,axis=0,return_inverse=True)
        support=np.unique(np.column_stack([inverse,source]),axis=0)
        counts=np.bincount(support[:,0].astype(int),minlength=len(unique))
        for group in components(unique[counts>=2]):
            if len(group)<12:continue
            low=group.min(axis=0);high=group.max(axis=0)
            if np.prod(high-low+3)>2_000_000:continue
            mask=np.zeros(tuple((high-low+3)[::-1]),np.uint8)
            pixels=group-low+1;mask[pixels[:,1],pixels[:,0]]=255
            contours,hierarchy=cv2.findContours(mask,cv2.RETR_CCOMP,cv2.CHAIN_APPROX_SIMPLE)
            if not contours:continue
            outer=max(range(len(contours)),key=lambda i:cv2.contourArea(contours[i]))
            if hierarchy[0,outer,2]!=-1:continue
            outline=cv2.approxPolyDP(contours[outer],.25,True)[:,0,:]
            if not 3<=len(outline)<=128:continue
            profile=(outline+low-1+.5)*cell
            rect=cv2.minAreaRect(profile.astype(np.float32));width,length=sorted(rect[1])
            if width<.1 or length<.3:continue
            cell_index={tuple(c):i for i,c in enumerate(unique)}
            selected=np.isin(inverse,[cell_index[tuple(c)] for c in group])
            underside=float(np.median(q[selected,1]))
            kind='beam' if length>=2.2*width and width<=1.3 else 'soffit'
            found.append(element(kind,profile,underside,float(ceiling_height),source[selected],capture_id,
                                 ['underside'],['top','sides','ends']))
    return found
