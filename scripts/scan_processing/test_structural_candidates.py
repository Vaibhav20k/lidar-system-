import unittest
import numpy as np
from structural_candidates import columns


def samples(circle=False):
    p=[];n=[];f=[]
    for frame in [0,12]:
        for y in np.arange(.2,2.8,.1):
            for t in np.linspace(0,1,80,endpoint=False):
                if circle:
                    theta=t*2*np.pi;p.append([.3*np.cos(theta),y,.3*np.sin(theta)]);n.append([np.cos(theta),0,np.sin(theta)]);f.append(frame)
                else:
                    for x,z,nx,nz in [(t*.6-.3,-.3,0,-1),(.3,t*.6-.3,1,0),(.3-t*.6,.3,0,1),(-.3,.3-t*.6,-1,0)]:
                        p.append([x,y,z]);n.append([nx,0,nz]);f.append(frame)
    return np.array(p),np.array(n),np.array(f)


class ColumnTests(unittest.TestCase):
    def test_rectangular_and_circular_columns_preserve_shape(self):
        for circle in [False,True]:
            found=columns(*samples(circle))
            self.assertEqual(len(found),1)
            self.assertEqual(found[0]['kind'],'column')
            self.assertEqual(found[0]['geometry']['type'],'cylinder' if circle else 'extrusion')
            self.assertEqual(found[0]['provenance']['frameIds'],[0,12])
            self.assertEqual(found[0]['review'],'candidate')

    def test_single_frame_and_short_furniture_do_not_establish_column(self):
        p,n,f=samples()
        self.assertEqual(columns(p,n,np.zeros(len(f))),[])
        use=p[:,1]<.7
        self.assertEqual(columns(p[use],n[use],f[use]),[])

    def test_separate_columns_are_not_merged(self):
        p,n,f=samples()
        found=columns(np.concatenate([p,p+[2,0,0]]),np.tile(n,(2,1)),np.tile(f,2))
        self.assertEqual(len(found),2)
