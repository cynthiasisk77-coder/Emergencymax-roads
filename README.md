# Emergency MAX road files

Street files for the offline step-by-step directions in the Emergency MAX app,
one pair per U.S. state (50 states and Washington, D.C.):

- `<state>-main.bin`: the roads a car follows between and through towns
- `<state>-local.bin`: every neighborhood street, in squares about 7 miles across

They are built from OpenStreetMap by `tools/routing/build_graph.py` and attached
to the `routes-v2` release. The app downloads the states it needs and finds the
way on the phone, with no signal.

Map files, one per state, are built the same way by `.github/workflows/map-tiles.yml`
(Planetiler, OpenMapTiles schema, zoom 0 to 14, PMTiles) and attached to the
`tiles-v1` release, with `tiles.json` listing every file and its size. The app
downloads a whole state as one file, so the download carries on after the app
is closed.

This repository holds map data only. It contains no app code and nothing personal.

## Data and license

Road data © OpenStreetMap contributors (https://www.openstreetmap.org/copyright),
available under the Open Database License (ODbL). The files here are a derived
database and are released under the same license. Extracts courtesy of Geofabrik.
