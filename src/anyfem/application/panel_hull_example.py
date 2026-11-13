"""File-driven structural vessel example. Licensed reference geometry is never bundled.

GDF coordinates are imported once into explicit owner vertex/edge IDs. Exact repeated input records identify shared vertices;
there is no tolerance search or subsequent reconstruction of owner topology. The submerged surface is retained, while the
open waterline is extended vertically to the declared deck. Scantlings, internal layout and attached deadweight are assumptions.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import hashlib
import math

import numpy as np


def planar_support(points):
    """A Plane whose bounded [0,1] parameter patch contains the entire authored polygon, including trapezoids."""
    from anygeometry import Plane
    points=np.asarray(points,dtype=float)
    u=points[1]-points[0];u/=np.linalg.norm(u)
    n=np.cross(u,points[-1]-points[0]);n/=np.linalg.norm(n)
    v=np.cross(n,u)
    local=np.column_stack(((points-points[0])@u,(points-points[0])@v))
    low,high=local.min(axis=0),local.max(axis=0)
    return Plane(points[0]+low[0]*u+low[1]*v,(high[0]-low[0])*u,(high[1]-low[1])*v)


def _symmetry_fan_seeds(panels,axis):
    """A cusp quad with three symmetry-plane corners must fan from its off-plane corner, not create an internal flat cap."""
    panels=np.array(panels,copy=True)
    for i,panel in enumerate(panels):
        on=panel[:,axis]==0.
        if on.sum()>=3 and not on.all():
            panels[i]=np.roll(panel,-int(np.flatnonzero(~on)[0]),axis=0)
    return panels


def read_gdf(path):
    path = Path(path)
    raw = path.read_bytes()
    lines = raw.decode('utf-8-sig').splitlines()
    try:
        unit, gravity = map(float, lines[1].split()[:2])
        sx, sy = map(int, lines[2].split()[:2])
        count = int(lines[3].split()[0])
        points = np.array([list(map(float, line.split())) for line in lines[4:] if line.strip()]) * unit
    except (ValueError, IndexError) as error:
        raise ValueError('invalid GDF header/coordinates') from error
    if points.shape != (4 * count, 3) or not np.isfinite(points).all() or unit <= 0 or (sx, sy) not in ((0, 0), (0, 1), (1, 0)):
        raise ValueError('unsupported GDF dimensions, units or symmetry')
    panels = points.reshape(-1, 4, 3)
    if sx:
        panels = _symmetry_fan_seeds(panels,0)
        panels = np.concatenate([panels, panels[:, [0,3,2,1]] * [-1, 1, 1]])
    if sy:
        panels = _symmetry_fan_seeds(panels,1)
        panels = np.concatenate([panels, panels[:, [0,3,2,1]] * [1, -1, 1]])
    # Reversing winding must KEEP vertex 0: a later triangle fan then uses the same diagonal on reflected warped quads.
    vertices, index, faces = [], {}, []
    for panel in panels:
        ring = []
        for p in panel:
            key = tuple(float(v) for v in p)
            if key not in index:
                index[key] = len(vertices); vertices.append(key)
            if not ring or ring[-1] != index[key]:
                ring.append(index[key])
        if len(ring) > 1 and ring[0] == ring[-1]:
            ring.pop()
        if len(set(ring)) < 3:
            raise ValueError('GDF panel collapsed to fewer than three vertices')
        faces.append(tuple(ring))
    V = np.array(vertices)
    wet = [f for f in faces if V[list(f), 2].min() < 0.]
    if any(V[list(f), 2].max() > 0. for f in wet):
        raise ValueError('example importer requires an explicitly panelled mean waterline at z=0')
    boundary = {}
    counts = Counter(tuple(sorted((a, b))) for f in wet for a, b in zip(f, f[1:] + f[:1]))
    if any(n > 2 for n in counts.values()):
        raise ValueError('non-manifold submerged GDF')
    for f in wet:
        for a, b in zip(f, f[1:] + f[:1]):
            if counts[tuple(sorted((a, b)))] == 1:
                if V[a, 2] != 0. or V[b, 2] != 0.:
                    raise ValueError('submerged GDF is open below its declared waterline')
                boundary[a] = b
    if len(boundary) < 4 or len(set(boundary.values())) != len(boundary):
        raise ValueError('waterline must be one simple closed loop')
    loop = [next(iter(boundary))]
    while boundary[loop[-1]] != loop[0]:
        loop.append(boundary[loop[-1]])
        if len(loop) > len(boundary):
            raise ValueError('waterline loop is not closed')
    if len(loop) != len(boundary):
        raise ValueError('multiple waterline loops unsupported')
    deck = float(V[:, 2].max())
    if deck <= 0:
        raise ValueError('reference contains no positive deck-height bound')
    return dict(vertices=V, wet_faces=wet, waterline=loop, deck_z=deck,
                source=dict(path=str(path.absolute()), sha256=hashlib.sha256(raw).hexdigest(), gravity=gravity,
                            symmetry=[sx, sy], input_panels=count, full_panels=len(faces)))


def build_structured_hull(path, *, mesh_size=1.5):
    """Create plating, frames, two transverse bulkheads, eight empty tanks and a two-level deckhouse.

    Tanks are empty structural compartments: no liquid mass, sloshing or internal fluid pressure is implied. Attached example
    deadweight is added separately, through ordinary Mass objects, after the FE mesh supplies the authoritative steel properties.
    """
    from anygeometry import OrientedEdge, EntityRef, Plane
    from anyfem import Project, steel
    from anyfem.model import BeamSection, WetSurface
    from anyfem.native_meshing import NativeMeshSettings

    source = read_gdf(path); V = source['vertices']; waterline = source['waterline']; deck = source['deck_z']
    keel = float(V[:, 2].min())
    if not (keel < -3 and deck > 2):
        raise ValueError('structured example requires at least 3 m draft and 2 m upper plating')
    p = Project(); p.name = 'L01 — structured floating vessel, tanks and deckhouse'
    p.add_material(steel('S355'))
    sections = {'Hull plating': .012, 'Main deck': .010, 'Transverse bulkheads': .008,
                'Tank boundaries': .008, 'Deckhouse plating': .006}
    for name, thickness in sections.items():
        p.add_plate_section(name, thickness, 'S355')
    g = p.geometry
    vertex_cache, edge_cache, face_cache = {}, {}, {}
    face_groups = defaultdict(set); member_edges = defaultdict(set); beam_direction = {}

    def vertex(xyz):
        key = tuple(float(x) for x in xyz)
        if key not in vertex_cache:
            vertex_cache[key] = g.add_point(*key)
        return vertex_cache[key]

    def edge(a, b):
        key = tuple(sorted((a, b)))
        if key not in edge_cache:
            edge_cache[key] = g.add_line(a, b)
        e = g.edges[edge_cache[key]]
        return OrientedEdge(e.id, e.start == a)

    def plate(points, group, section, *, triangulate=False):
        ids = [vertex(q) for q in points]
        ids = [v for i, v in enumerate(ids) if i == 0 or ids[i-1] != v]
        if ids[-1] == ids[0]: ids.pop()
        if len(set(ids)) < 3: return []
        if triangulate and len(ids) > 3:
            made = []
            for i in range(1, len(ids)-1):
                made += plate([g.vertex_position(v) for v in (ids[0], ids[i], ids[i+1])], group, section)
            return made
        key = tuple(sorted(ids))
        if key not in face_cache:
            ring = tuple(edge(a, b) for a, b in zip(ids, ids[1:]+ids[:1]))
            support = planar_support([g.vertex_position(i) for i in ids])
            fid = g.add_face_from_loop(ring, (0,1,2,3) if len(ids)==4 else None, surface=support)
            face_cache[key] = fid
            face_groups[section].add(fid)
        fid = face_cache[key]; face_groups[group].add(fid)
        return [fid]

    def member(a, b, group, direction):
        aid, bid = vertex(a), vertex(b)
        if aid == bid: return
        e = edge(aid, bid).edge
        member_edges[group].add(e)
        beam_direction[e] = tuple(float(x) for x in direction)

    widths = {}
    for i in waterline:
        x, y, _ = V[i]
        widths[float(x)] = max(widths.get(float(x), 0.), abs(float(y)))
    xs = sorted(widths); centre_x = .5*(xs[0]+xs[-1]); length = xs[-1]-xs[0]
    pick = lambda fraction: min(xs, key=lambda x: abs(x-(xs[0]+fraction*length)))
    tank_x = sorted({pick(f) for f in (.3, .4, .5, .6, .7)})
    if len(tank_x) != 5: raise ValueError('reference has too few longitudinal stations for the eight-tank layout')
    bottom_ys = [set(abs(float(y)) for x, y, z in V if x == station and z == keel) for station in tank_x]
    common = set.intersection(*bottom_ys)
    candidates = [y for y in common if 0 < y < .8*min(widths[x] for x in tank_x)]
    if not candidates: raise ValueError('no common keel strake for tank foundations')
    tank_y = min(candidates, key=lambda y: abs(y-.5*min(widths[x] for x in tank_x)))
    floor, top = keel+.46, keel+2.16
    tank_rows = (-tank_y, 0., tank_y)
    tank_manifest = []
    cargo_vertices = []
    raw_edges = set(tuple(sorted((a,b))) for f in source['wet_faces'] for a,b in zip(f,f[1:]+f[:1]))
    with g.transaction():
        # Triangulate warped GDF quads without changing any source vertex or wet boundary.
        for f in source['wet_faces']:
            plate(V[list(f)], 'Exterior submerged hull', 'Hull plating', triangulate=True)
        for a, b in raw_edges:
            A, B = V[a], V[b]
            direction = .5*(A+B)-[centre_x, 0., -.5]
            if A[0] == B[0]:
                direction[0] = 0.  # frame webs lie in their transverse station plane
                member(A, B, 'Transverse frames', -direction)
            elif abs(B[0]-A[0]) > abs(B[1]-A[1]):
                member(A, B, 'Hull longitudinals', -direction)
        for a, b in zip(waterline, waterline[1:]+waterline[:1]):
            A, B = V[a], V[b]
            for lo, hi in ((0., deck/2), (deck/2, deck)):
                plate([(B[0],B[1],lo),(A[0],A[1],lo),(A[0],A[1],hi),(B[0],B[1],hi)],
                      'Upper side plating', 'Hull plating')
            for h in (0., deck/2, deck):
                member((A[0],A[1],h),(B[0],B[1],h),'Upper longitudinals',(0.,-np.sign(A[1] or B[1]),0.))
            member((A[0],A[1],0.),(A[0],A[1],deck/2),'Transverse frames',(0.,-np.sign(A[1]),0.))
            member((A[0],A[1],deck/2),(A[0],A[1],deck),'Transverse frames',(0.,-np.sign(A[1]),0.))
        # Deck strips match both waterline endpoints at every source station.
        fractions = (-1., -.5, 0., .5, 1.)
        for x0,x1 in zip(xs,xs[1:]):
            if x0==xs[0] and widths[x0]>0:
                # The reference transom has two half-width edges, not the interior deck's four strakes.
                for sign in (-1.,1.):
                    A=(x0,sign*widths[x0],deck);B=(x0,0.,deck)
                    C=(x1,0.,deck);D=(x1,sign*.5*widths[x1],deck);E=(x1,sign*widths[x1],deck)
                    for tri in ((A,E,D),(A,D,B),(B,D,C)):
                        plate(tri,'Main deck','Main deck')
            else:
                for y0,y1 in zip(fractions,fractions[1:]):
                    plate([(x0,y0*widths[x0],deck),(x1,y0*widths[x1],deck),(x1,y1*widths[x1],deck),(x0,y1*widths[x0],deck)],
                          'Main deck', 'Main deck')
            for y in fractions:
                start_y=np.sign(y) if x0==xs[0] and abs(y)==.5 else y
                member((x0,start_y*widths[x0],deck),(x1,y*widths[x1],deck),'Deck longitudinals',(0,0,-1))
        for x in xs:
            row=(-1.,0.,1.) if x==xs[0] else fractions
            for a,b in zip(row,row[1:]):
                member((x,a*widths[x],deck),(x,b*widths[x],deck),'Deck transverse beams',(0,0,-1))
        # Full-depth transverse diaphragms use the exact imported shell perimeter edges.
        for station in (pick(.25),pick(.75)):
            links = defaultdict(list)
            for a,b in raw_edges:
                if V[a,0] == V[b,0] == station:
                    links[a].append(b); links[b].append(a)
            ends = [i for i,a in links.items() if len(a)==1]
            if len(ends)!=2 or any(len(a)>2 for a in links.values()):
                raise ValueError('bulkhead station is not one conforming hull-section chain')
            chain=[min(ends,key=lambda i:V[i,1])]
            while chain[-1] != max(ends,key=lambda i:V[i,1]):
                chain.append(next(i for i in links[chain[-1]] if len(chain)<2 or i != chain[-2]))
            rim=[V[i].tolist() for i in chain]
            yhi,ylo=rim[-1][1],rim[0][1]
            rim += [[station,yhi,deck/2],[station,yhi,deck]]
            rim += [[station,f*widths[station],deck] for f in (.5,0.,-.5,-1.)]
            rim += [[station,ylo,deck/2]]
            centre=(station,0.,.5*(keel+deck))
            for A,B in zip(rim,rim[1:]+rim[:1]):
                plate([A,B,centre],f'Transverse bulkhead X={station:g}','Transverse bulkheads')
        # Eight closed empty compartments; shared tank partitions are authored only once.
        for bay,(left,right) in enumerate(zip(tank_x,tank_x[1:]),1):
            local=[x for x in xs if left<=x<=right]
            for side,(yl,yr) in enumerate(zip(tank_rows,tank_rows[1:]),1):
                name=f'Tank {bay:02d} '+('port' if side==2 else 'starboard')
                ids=[]
                for x0,x1 in zip(local,local[1:]):
                    ids+=plate([(x0,yl,floor),(x0,yr,floor),(x1,yr,floor),(x1,yl,floor)],name,'Tank boundaries')
                    ids+=plate([(x0,yl,top),(x1,yl,top),(x1,yr,top),(x0,yr,top)],name,'Tank boundaries')
                    for y in (yl,yr):
                        ids+=plate([(x0,y,floor),(x1,y,floor),(x1,y,top),(x0,y,top)],name,'Tank boundaries')
                for x in (left,right):
                    ids+=plate([(x,yl,floor),(x,yr,floor),(x,yr,top),(x,yl,top)],name,'Tank boundaries')
                tank_manifest.append(dict(name=name,bounds_m=[[left,right],[yl,yr],[floor,top]],volume_m3=(right-left)*(yr-yl)*(top-floor),
                                          face_ids=sorted(set(ids)),fill_fraction=0.,contents='Empty; no liquid pressure, mass or sloshing'))
        for x in [x for x in xs if tank_x[0]<=x<=tank_x[-1]]:
            for y in tank_rows:
                if (x,y,keel) not in vertex_cache: raise ValueError('tank foundation misses an exact imported keel vertex')
                member((x,y,keel),(x,y,floor),'Tank foundations',(1,0,0))
                levels=(top,keel+3.16,keel+6.16)
                for previous,z in zip(levels,levels[1:]):
                    member((x,y,previous),(x,y,z),'Deadweight support posts',(1,0,0))
                    cargo_vertices.append(vertex((x,y,z)))
        # Tapered deckhouse footprint shares the main-deck strakes; two occupied levels and a roof.
        house=[x for x in xs if pick(.1)<=x<=pick(.25)]
        for lo,hi in ((deck,deck+3.),(deck+3.,deck+6.)):
            for x0,x1 in zip(house,house[1:]):
                for sign in (-.5,.5):
                    plate([(x0,sign*widths[x0],lo),(x1,sign*widths[x1],lo),(x1,sign*widths[x1],hi),(x0,sign*widths[x0],hi)],
                          'Deckhouse walls','Deckhouse plating',triangulate=True)
                for a,b in ((-.5,0.),(0.,.5)):
                    plate([(x0,a*widths[x0],hi),(x1,a*widths[x1],hi),(x1,b*widths[x1],hi),(x0,b*widths[x0],hi)],
                          'Deckhouse decks','Deckhouse plating')
            for x in (house[0],house[-1]):
                for a,b in ((-.5,0.),(0.,.5)):
                    plate([(x,a*widths[x],lo),(x,b*widths[x],lo),(x,b*widths[x],hi),(x,a*widths[x],hi)],
                          'Deckhouse end walls','Deckhouse plating')
            for x in house:
                for y in (-.5,0.,.5):
                    member((x,y*widths[x],lo),(x,y*widths[x],hi),'Deckhouse frames',(1,0,0))
                for a,b in ((-.5,0.),(0.,.5)):
                    member((x,a*widths[x],hi),(x,b*widths[x],hi),'Deckhouse beams',(0,0,-1))
        for group, ids in face_groups.items():
            g.add_to_group(group,[EntityRef('face',i) for i in sorted(ids)])
        for group, ids in member_edges.items():
            g.add_to_group(group,[EntityRef('edge',i) for i in sorted(ids)])
        # Author continuous physical axes, rather than one unrelated Member per small input-panel edge.
        # Split at real branches; split closed frames into two open chains. Every physical edge is owned once.
        owned=set()
        order=('Deadweight support posts','Tank foundations','Transverse frames','Deck transverse beams','Deckhouse frames',
               'Hull longitudinals','Upper longitudinals','Deck longitudinals','Deckhouse beams')
        for group in order:
            remaining=set(member_edges[group])-owned
            if not remaining: continue
            adjacency=defaultdict(set)
            for eid in remaining:
                e=g.edges[eid];adjacency[e.start].add(eid);adjacency[e.end].add(eid)
            chains=[]
            while remaining:
                endpoints=sorted(v for v,items in adjacency.items() if len(items)!=2 and items&remaining)
                current=endpoints[0] if endpoints else g.edges[min(remaining)].start
                start=current;chain=[]
                while True:
                    options=adjacency[current]&remaining
                    if not options:break
                    eid=min(options);remaining.remove(eid);e=g.edges[eid]
                    chain.append(OrientedEdge(eid,e.start==current));current=e.end if e.start==current else e.start
                    if current==start or len(adjacency[current])!=2:break
                if current==start and len(chain)>1:
                    cut=len(chain)//2;chains.extend((tuple(chain[:cut]),tuple(chain[cut:])))
                else:chains.append(tuple(chain))
            part=g.add_part(name=group)
            g.add_members(chains,part_id=part,names=[f'{group} {i+1:03d}' for i in range(len(chains))])
            owned.update(member_edges[group])
    # Grouped section assignment and one public owner transaction avoid repeated ownership snapshots.
    profiles={}; already=set(); assignment_groups=defaultdict(list)
    for group in ('Deadweight support posts','Tank foundations','Transverse frames','Deck transverse beams','Deckhouse frames',
                  'Hull longitudinals','Upper longitudinals','Deck longitudinals','Deckhouse beams'):
        for eid in sorted(member_edges[group]-already):
            axis=np.array(g.vertex_position(g.edges[eid].end))-g.vertex_position(g.edges[eid].start)
            d=np.array(beam_direction[eid],dtype=float);d-=axis*np.dot(d,axis)/np.dot(axis,axis)
            if np.linalg.norm(d)<1e-10: d=np.cross(axis,[0,0,1])
            if np.linalg.norm(d)<1e-10: d=np.cross(axis,[0,1,0])
            d/=np.linalg.norm(d)
            heavy=group in ('Deadweight support posts','Tank foundations','Transverse frames','Deck transverse beams')
            key=(heavy,tuple(np.round(d,10)))
            if key not in profiles:
                name=f'{"Frame T400" if heavy else "Stiffener T180"} orientation {len(profiles)+1}'
                p.add_beam_section(BeamSection(name=name,profile='T-bar',material='S355',web_height=.4 if heavy else .18,
                    web_thickness=.012 if heavy else .008,flange_width=.18 if heavy else .08,
                    flange_thickness=.016 if heavy else .010,web_direction=tuple(d),offset_mode='centerline'))
                profiles[key]=name
            assignment_groups[profiles[key]].append(eid);already.add(eid)
    with g.transaction():
        for section in sections:
            if face_groups[section]: p.assign_plates(sorted(face_groups[section]),section)
        for section,ids in assignment_groups.items():
            p.assign_beams(ids,section)
    wet_ids=sorted(face_groups['Exterior submerged hull']|face_groups['Upper side plating'])
    for fid in wet_ids:
        p.add_wet_surface(WetSurface(p.face(fid),name=f'Exterior hull {fid}',fluid_side='positive_normal'))
    p.target_size=float(mesh_size)
    # Explicit coarse reference-facet discretization. Shared plate/beam edges use the SAME count; later refinement must be
    # a coherent seed change, rather than independent automatic plate and beam overrides fighting on opposite sides.
    p.seeding_overrides={int(e):1 for e in g.edges}
    p.set_native_mesh_settings(NativeMeshSettings.create(mesh_size,element_order='quadratic',backend='quad_first',
        parameters={'layout_policy':'adaptive','quad_t3_angle_action':'warning'}))
    manifest=dict(schema='anyfem-structured-l01-v1',source=source['source'],
        geometry_assumptions=['Submerged GDF vertices/facets retained; warped quads triangulated.',
            'Upper sides rebuilt vertically from the exact submerged waterline to the reference deck height.',
            'Deck, internal layout, scantlings, deckhouse and attached deadweight are illustrative assumptions, not L01 scantlings.'],
        mean_reference_waterline_z_m=0.,keel_z_m=keel,deck_z_m=deck,deckhouse_roof_z_m=deck+6.,
        groups={name:sorted(ids) for name,ids in face_groups.items()},members={name:sorted(ids) for name,ids in member_edges.items()},
        tanks=tank_manifest,cargo_vertex_ids=cargo_vertices,wet_face_ids=wet_ids,closing_face_ids=sorted(face_groups['Main deck']),
        sections_m=sections,requested_mesh_size_m=mesh_size,
        seeding='One quadratic division per authored facet/member segment; shared plate/beam counts pinned consistently.')
    return p,manifest


def add_reference_deadweight(project, mesh, manifest, reference_mass, reference_cg):
    """Positive attached masses match total displacement/CG; actual FE inertia is retained, never copied from reference RAOs."""
    from anyfem.application.floating_mass import dry_mass_properties
    from anyfem.solve.build import build_fe_model
    from anyfem.model import Mass
    steel=dry_mass_properties(build_fe_model(project,mesh,require_loads=False,require_supports=False).fe_model)
    remaining=float(reference_mass)-steel.mass
    if remaining<=0: raise ValueError('reference target leaves no positive explicit deadweight')
    target=(float(reference_mass)*np.asarray(reference_cg)-steel.mass*steel.cg)/remaining
    ids=manifest['cargo_vertex_ids']; xyz=np.array([project.geometry.vertex_position(i) for i in ids])
    # Maximum-entropy positive weights with exact first moments; the target must lie strictly inside these attachment positions.
    from scipy.optimize import root
    scale=np.maximum(np.ptp(xyz,axis=0),1.)
    centred=(xyz-target)/scale
    def weights(lam):
        a=centred@lam;a-=a.max();w=np.exp(a);return w/w.sum()
    solution=root(lambda lam:weights(lam)@centred,np.zeros(3))
    w=weights(solution.x)
    if not solution.success or np.linalg.norm(w@centred)>1e-10 or w.min()<=0:
        raise ValueError('reference mass/CG cannot be represented by positive attached example deadweight')
    for i,weight in zip(ids,w):
        project.add_mass(Mass(project.point(i),remaining*float(weight),name=f'Example fixed deadweight at support {i}'))
    final=dry_mass_properties(build_fe_model(project,mesh,require_loads=False,require_supports=False).fe_model)
    if abs(final.mass-reference_mass)>1e-8*reference_mass or np.linalg.norm(final.cg-reference_cg)>1e-7:
        raise ValueError('assembled explicit mass ledger misses its declared target')
    manifest['mass_ledger']=dict(structural_steel_mass_kg=float(steel.mass),structural_cg_m=steel.cg.tolist(),
        fixed_deadweight_mass_kg=remaining,deadweight_item_count=len(ids),total_mass_kg=float(final.mass),cg_m=final.cg.tolist(),
        inertia_cg_kg_m2=final.inertia_cg.tolist(),reference_mass_target_kg=float(reference_mass),reference_cg_target_m=list(reference_cg),
        limitation='Explicit rigidly attached example deadweight; tanks empty. Actual assembled inertia differs from the reference condition.')
    return final
