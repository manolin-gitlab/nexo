"""Build the equirectangular basemap from world-atlas@2.0.2's land-110m.json.
Usage: python scripts/build-world.py /path/to/world-atlas-2.0.2.tgz
Natural Earth public-domain data; world-atlas ISC license is preserved.
"""
import json,sys,tarfile
from pathlib import Path
root=Path(__file__).resolve().parents[1]/'dist'
with tarfile.open(sys.argv[1]) as archive:
    data=json.load(archive.extractfile('package/land-110m.json'))
    (root/'world-atlas-LICENSE.txt').write_bytes(archive.extractfile('package/LICENSE').read())
sx,sy=data['transform']['scale'];tx,ty=data['transform']['translate'];arcs=[]
for arc in data['arcs']:
    x=y=0;points=[]
    for dx,dy in arc:
        x+=dx;y+=dy;points.append((x*sx+tx,y*sy+ty))
    arcs.append(points)
def clip(points,bound,keep_left):
    if not points:return []
    out=[]
    for a,b in zip(points[-1:]+points[:-1],points):
        ai=a[0]<=bound if keep_left else a[0]>=bound
        bi=b[0]<=bound if keep_left else b[0]>=bound
        if ai!=bi:
            f=(bound-a[0])/(b[0]-a[0]);out.append((bound,a[1]+f*(b[1]-a[1])))
        if bi:out.append(b)
    return out
def ring(indices):
    points=[]
    for i in indices:
        a=arcs[i] if i>=0 else list(reversed(arcs[~i]))
        points.extend(a if not points else a[1:])
    unwrapped=[]
    for lon,lat in points:
        if unwrapped:
            while lon-unwrapped[-1][0]>180:lon-=360
            while lon-unwrapped[-1][0]<-180:lon+=360
        unwrapped.append((lon,lat))
    if abs(unwrapped[-1][0]-unwrapped[0][0])>180:
        pole=-90 if sum(p[1] for p in unwrapped)<0 else 90
        unwrapped.extend([(unwrapped[-1][0],pole),(unwrapped[0][0],pole)])
    result=''
    for shift in (-720,-360,0,360,720):
        p=[(lon+shift,lat) for lon,lat in unwrapped]
        p=clip(clip(p,-180,False),180,True)
        if len(p)>2:
            result+='M'+'L'.join(f'{(lon+180)/360*1000:.2f},{(90-lat)/180*450:.2f}' for lon,lat in p)+'Z'
    return result
def geom(g):
    if g['type']=='GeometryCollection':return ''.join(geom(v) for v in g['geometries'])
    if g['type']=='Polygon':return ''.join(ring(v) for v in g['arcs'])
    if g['type']=='MultiPolygon':return ''.join(ring(r) for p in g['arcs'] for r in p)
    raise ValueError(g['type'])
path=geom(data['objects']['land'])
(root/'world.svg').write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 450"><title>Natural Earth land, 110m, equirectangular projection</title><path d="{path}" fill="#24363b" stroke="#35494d" stroke-width=".55" fill-rule="evenodd"/></svg>')
print('Map ready, antimeridian clipped.')
