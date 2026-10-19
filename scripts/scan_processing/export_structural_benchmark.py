"""Export canonical room candidates for the independent benchmark scorer.

Usage: python export_structural_benchmark.py room.json predictions.json
No reference dimensions are read. Inferred dimensions cannot pass measurement
gates. Shape/pose are included for audit, but current scorer does not certify them.
"""
import json
import sys
from pathlib import Path
import cv2
import numpy as np


def convert(room):
    result=[]
    for e in room.get('structures',[]):
        if e['review']=='rejected':continue
        g=e['geometry'];inferred=[]
        length=g['sections'][-1]['at'] if g['type']=='sections' else g['length']
        center_xz=np.zeros(2)
        if g['type']=='cylinder':dimensions={'diameter':2*g['radius'],'height':length}
        else:
            profile=g['sections'][0]['profile'] if g['type']=='sections' else g['profile']
            xy=np.array([[p['x'],p['z']] for p in profile],np.float32)
            rectangle=cv2.minAreaRect(xy);center_xz=np.array(rectangle[0])
            width,depth=sorted(rectangle[1])
            dimensions={'width':float(width),'depth':float(depth),'height':length}
        # Names describe the local profile axes, not a claim that every beam's
        # local extrusion is its horizontal run. Reference labels must agree.
        if any(f in e['coverage']['inferredFaces'] for f in ['top','bottom']):inferred.append('height')
        if any(f.startswith('side') or f in ['ends','lateral'] for f in e['coverage']['inferredFaces']):
            inferred.extend(k for k in dimensions if k!='height')
        if g['type']=='sections':inferred=list(dimensions) # varying section requires section-specific references
        x,y,z,w=e['rotation']
        rotation=np.array([[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],
                           [2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],
                           [2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]])
        origin=np.array([e['origin'][k] for k in ['x','y','z']])
        result.append({'id':e['id'],'kind':e['kind'],'center':(origin+rotation@np.array([center_xz[0],length/2,center_xz[1]])).tolist(),
                       'dimensions':dimensions,'inferredDimensions':inferred,'geometry':g,
                       'coverage':e['coverage'],'provenance':e['provenance']})
    return {'version':1,'units':'meters','elements':result}


if __name__=='__main__':
    room=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    with Path(sys.argv[2]).open('x',encoding='utf-8') as out:json.dump(convert(room),out,indent=2,allow_nan=False)
