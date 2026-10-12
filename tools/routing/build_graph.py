"""Builds the offline driving-directions files for one U.S. state.

Cynthia, 12 October 2026: step-by-step directions from A to B that work with
no signal once downloaded, on every street, not only the main roads.

Two files per state, from an OpenStreetMap extract already cut down to roads
by `osmium tags-filter`:

  <slug>-main.bin   every road a car follows between and through towns
                    (motorway down to unclassified). Loaded whole; this is
                    what long trips run on.
  <slug>-local.bin  every neighborhood street (residential, living_street,
                    road), cut into squares about 7 miles across. The app
                    reads only the squares around where you start and where
                    you are going, so a whole state of side streets never has
                    to sit in the phone's memory at once.

Every crossing carries its OpenStreetMap node id, so a side street joins the
main road it meets, and one state's roads join the next state's at the
border.

Usage: python build_graph.py roads.osm.pbf out-prefix "Texas"
       (writes out-prefix-main.bin and out-prefix-local.bin)
"""
import json
import math
import struct
import sys
from collections import defaultdict

import osmium

# class id, default speed km/h
MAIN = {
    'motorway': (0, 105), 'motorway_link': (1, 60),
    'trunk': (2, 90), 'trunk_link': (3, 55),
    'primary': (4, 75), 'primary_link': (5, 50),
    'secondary': (6, 65), 'secondary_link': (7, 45),
    'tertiary': (8, 55), 'tertiary_link': (9, 40),
    'unclassified': (10, 45),
}
LOCAL = {
    'residential': (11, 35),
    'living_street': (12, 15),
    'road': (13, 35),
}
NO_ACCESS = {'no', 'private'}
SIMPLIFY_M = 12.0
TILE_DEG = 0.1


def meters(a, b):
    lat1, lon1 = a
    lat2, lon2 = b
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = rlat2 - rlat1
    dlon = math.radians(lon2 - lon1)
    h = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return 6371000.0 * 2 * math.asin(min(1.0, math.sqrt(h)))


def speed_kmh(tags, default):
    raw = tags.get('maxspeed', '')
    try:
        if raw.endswith('mph'):
            return max(10, min(130, int(float(raw[:-3].strip()) * 1.609)))
        if raw:
            return max(10, min(130, int(float(raw.split()[0]))))
    except ValueError:
        pass
    return default


def point_segment_m(p, a, b):
    kx = 111320.0 * math.cos(math.radians(p[0]))
    ky = 110540.0
    ax, ay = (a[1] - p[1]) * kx, (a[0] - p[0]) * ky
    bx, by = (b[1] - p[1]) * kx, (b[0] - p[0]) * ky
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / L))
    return math.hypot(ax + t * dx, ay + t * dy)


def simplify(points, tol):
    if len(points) <= 2:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        i, j = stack.pop()
        best, idx = 0.0, -1
        for k in range(i + 1, j):
            d = point_segment_m(points[k], points[i], points[j])
            if d > best:
                best, idx = d, k
        if best > tol and idx > 0:
            keep[idx] = True
            stack.append((i, idx))
            stack.append((idx, j))
    return [p for p, k in zip(points, keep) if k]


class Roads(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.ways = []
        self.use = defaultdict(int)

    def way(self, w):
        hw = w.tags.get('highway')
        table = MAIN if hw in MAIN else LOCAL if hw in LOCAL else None
        if table is None:
            return
        if w.tags.get('access') in NO_ACCESS or w.tags.get('motor_vehicle') in NO_ACCESS or w.tags.get('area') == 'yes':
            return
        try:
            pts = [(n.ref, n.location.lat, n.location.lon) for n in w.nodes]
        except osmium.InvalidLocationError:
            return
        if len(pts) < 2:
            return
        cls, default = table[hw]
        oneway = w.tags.get('oneway', '')
        fwd, back = True, True
        if oneway in ('yes', 'true', '1') or (hw in ('motorway', 'motorway_link') and oneway != 'no') or w.tags.get('junction') in ('roundabout', 'circular'):
            back = False
        elif oneway == '-1':
            fwd = False
        self.ways.append((pts, cls, speed_kmh(w.tags, default), w.tags.get('name', ''), w.tags.get('ref', ''), fwd, back))
        for i, (ref_id, _, _) in enumerate(pts):
            self.use[ref_id] += 2 if i in (0, len(pts) - 1) else 1


class Graph:
    """One self-contained road graph: crossings with their OSM ids, roads between them."""

    def __init__(self):
        self.vertex_of = {}
        self.vid, self.vlat, self.vlon = [], [], []
        self.names, self.name_index = [''], {'': 0}
        self.e = defaultdict(list)
        self.shape = []

    def vertex(self, ref_id, lat, lon):
        v = self.vertex_of.get(ref_id)
        if v is None:
            v = len(self.vid)
            self.vertex_of[ref_id] = v
            self.vid.append(ref_id)
            self.vlat.append(lat)
            self.vlon.append(lon)
        return v

    def name_id(self, name, ref):
        label = name if not ref else (f'{ref} ({name})' if name and ref not in name else ref)
        i = self.name_index.get(label)
        if i is None:
            i = len(self.names)
            self.name_index[label] = i
            self.names.append(label)
        return i

    def add(self, a, b, seg, length, cls, spd, name, ref, fwd, back):
        u = self.vertex(*a)
        v = self.vertex(*b)
        inner = simplify(seg, SIMPLIFY_M)[1:-1]
        e = self.e
        e['from'].append(u); e['to'].append(v)
        e['len'].append(int(round(length)))
        e['name'].append(self.name_id(name, ref))
        e['shape_start'].append(len(self.shape)); e['shape_count'].append(len(inner))
        e['flags'].append((1 if fwd else 0) | (2 if back else 0))
        e['cls'].append(cls); e['speed'].append(spd)
        self.shape.extend(inner)

    def blob(self, region, extra=None):
        header = {
            'format': 'emax-route-v2', 'region': region,
            'vertices': len(self.vid), 'edges': len(self.e['from']), 'shape': len(self.shape),
            'names': self.names,
        }
        if extra:
            header.update(extra)
        hjson = json.dumps(header, separators=(',', ':')).encode('utf-8')
        pad = (4 - (8 + len(hjson)) % 4) % 4
        out = bytearray(b'EMR2')
        out += struct.pack('<I', len(hjson) + pad)
        out += hjson + b' ' * pad

        def arr(fmt, values):
            out.extend(struct.pack(f'<{len(values)}{fmt}', *values))

        def u8(values):
            out.extend(bytes(values))
            out.extend(b'\0' * ((4 - len(values) % 4) % 4))

        e = self.e
        arr('i', [int(round(x * 1e6)) for x in self.vlat])
        arr('i', [int(round(x * 1e6)) for x in self.vlon])
        arr('I', [x & 0xFFFFFFFF for x in self.vid])
        arr('I', [x >> 32 for x in self.vid])
        for key in ('from', 'to', 'len', 'name', 'shape_start', 'shape_count'):
            arr('I', e[key])
        for key in ('flags', 'cls', 'speed'):
            u8(e[key])
        arr('i', [int(round(p[0] * 1e6)) for p in self.shape])
        arr('i', [int(round(p[1] * 1e6)) for p in self.shape])
        return bytes(out)


def main(src, prefix, region):
    h = Roads()
    h.apply_file(src, locations=True, idx='flex_mem')

    main_graph = Graph()
    tiles = defaultdict(Graph)
    for pts, cls, spd, name, ref, fwd, back in h.ways:
        start = 0
        for i in range(1, len(pts)):
            last = i == len(pts) - 1
            if not last and h.use.get(pts[i][0], 0) < 2:
                continue
            seg = [(p[1], p[2]) for p in pts[start:i + 1]]
            length = sum(meters(seg[k], seg[k + 1]) for k in range(len(seg) - 1))
            if length >= 0.5:
                a, b = pts[start], pts[i]
                if cls <= 10:
                    main_graph.add(a, b, seg, length, cls, spd, name, ref, fwd, back)
                else:
                    mid = seg[len(seg) // 2]
                    key = (math.floor(mid[0] / TILE_DEG), math.floor(mid[1] / TILE_DEG))
                    tiles[key].add(a, b, seg, length, cls, spd, name, ref, fwd, back)
            start = i

    main_blob = main_graph.blob(region, {'kind': 'main'})
    with open(f'{prefix}-main.bin', 'wb') as f:
        f.write(main_blob)

    # Local file: an index of squares, then each square's own graph.
    index = []
    blobs = []
    offset = 0
    for (r, c), g in sorted(tiles.items()):
        b = g.blob(region, {'kind': 'local-tile', 'tile': [r, c]})
        b += b'\0' * ((4 - len(b) % 4) % 4)
        index.append([r, c, offset, len(b)])
        blobs.append(b)
        offset += len(b)
    header = json.dumps({'format': 'emax-local-v2', 'region': region, 'tileDeg': TILE_DEG, 'tiles': index}, separators=(',', ':')).encode('utf-8')
    pad = (4 - (8 + len(header)) % 4) % 4
    with open(f'{prefix}-local.bin', 'wb') as f:
        f.write(b'EML2')
        f.write(struct.pack('<I', len(header) + pad))
        f.write(header + b' ' * pad)
        for b in blobs:
            f.write(b)
    print(json.dumps({
        'region': region,
        'main': {'vertices': len(main_graph.vid), 'edges': len(main_graph.e['from']), 'bytes': len(main_blob)},
        'local': {'tiles': len(index), 'edges': sum(len(g.e['from']) for g in tiles.values()), 'bytes': offset},
    }))


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2], sys.argv[3])
