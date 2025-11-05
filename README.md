# SpatialRepresentationURN — OSMnx-powered GUI Fork

This fork adds a desktop GUI and an OSMnx-based ingestion pipeline on top of the original SpatialRepresentationURN project. It lets you search and download OpenStreetMap road networks, or load local shapefiles, preview them in synchronized side-by-side maps, and then convert to the URN representation using the project’s original graph-building routines—all in one place and packaged for easy distribution.

What’s included

OSMnx integration: geocode a place, fetch the street network (point buffer or polygon), and convert to GeoDataFrames.

Powered by OSMnx (Geoff Boeing) — repo: https://github.com/gboeing/osmnx

Local data support: load .shp, and auto-generate stable full_ids when missing.

Fast intersection detection: Shapely 2.x STRtree with predicate="intersects" plus multiprocessing workers (WKB-based payloads) to collect split nodes robustly and quickly on large graphs. Safe fallback for older builds.

Dual-panel viewer: Matplotlib/Tkinter viewer with linked pan/zoom for original vs. converted networks.

One-click conversion & export: runs Crossing_Checking and GraphBuilder, saves the URN assets, and additionally exports:

*.geojson: URN lines (EPSG:4326)

*_ORIGINAL.geojson: original input lines (EPSG:4326)

User-friendly UI: progress bar, live log, and minimal setup friction.

Packaging-friendly: designed to bundle with PyInstaller (icons/assets handled via resource_path; Shapely/pyproj/fiona hooks supported by _pyinstaller_hooks_contrib).

# A Global Urban Road Network Self-Adaptive Simplification Workflow from Traffic to Spatial Representation

Urban road network is crucial for understanding and revealing the spatial logic of urban organization and evolution. However, existing urban road network datasets like OpenStreetMap are designed for traffic studies, treating each lane as a distinct spatial unit of mobility, which may not align with urban studies considering each road as an integration space for social and cultural dynamics. This study established a novel workflow to self-adaptively transform the global urban road network from traffic representation to spatial representation. Our workflow, comprising six critical stages, is anchored on segment divergence from their surroundings to guide aggregation decisions, effectively mitigating the risks of over-aggregation and under-aggregation against the diversity of global urban backgrounds. This workflow is expected to become a robust data layer for urban socio-economic modelling and GeoAI development.

To use this workflow, plz check the **Simplification_Process** and use the **main.py**. A test-used SHP file is provided to help users test our scripts' output. As shown figs below, the multi-lane roads are aggregated to singular spatial entities without moving their location or distorting their geometries.
![image](https://github.com/user-attachments/assets/b3cf3396-5d17-436b-afdf-61c7c5736335)


This workflow has been tasted by 35 global representative URNs spanning various continents across different urban backgrounds. This workflow significantly reduces the duplicated segments of roads from an average of 31.2% to 3.6% in total, performing consistently across diverse countries and continents.
![image](https://github.com/user-attachments/assets/4c71bcd4-3437-43b2-b9ab-2ec771d277e5)
