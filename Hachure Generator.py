import math
import statistics
import random
from datetime import datetime

from collections import defaultdict

from qgis.PyQt.QtWidgets import QApplication, QMessageBox

from qgis.utils import iface
from qgis.core import (
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
    QgsPointXY,
    QgsGeometry,
    QgsFeature,
    QgsWkbTypes,
    edit,
)
from qgis import processing

from tools import tools


def fcnExpScale(val, domainMin, domainMax, rangeMin, rangeMax, exponent):
    if val is None or (domainMin >= domainMax) or (exponent <= 0):
        return None

    if val >= domainMax:
        return rangeMax
    elif val <= domainMin:
        return rangeMin

    return (
        (float(rangeMax) - float(rangeMin)) / math.pow(domainMax - domainMin, exponent)
    ) * math.pow(float(val) - domainMin, exponent) + rangeMin


# ============================USER PARAMETERS============================
# These two params below are in DEM pixel units. So choosing 6 for the
# max_hachure density means the script aims to make hachures 6 px apart
# when the slope is at its minimum

# default : pixel units
params = {"minhs":5, "maxhs":50, "mins":None, "maxs":None, "minslope":15, "maxslope":60, "checks":100, "shift":1}
# map units
# params = {"minhs":None, "maxhs":None, "mins":20, "maxs":100, "minslope":15, "maxslope":50, "checks":300, "shift":0.9}
# miglos 1m. Pixels units
# params = {"minhs":3, "maxhs":30, "mins":None, "maxs":None, "minslope":15, "maxslope":60, "checks":300, "shift":0.8}

min_hachure_spacing = params["minhs"]
max_hachure_spacing = params["maxhs"]
min_spacing = params["mins"]
max_spacing = params["maxs"]

# faire glisser la répartition des pentes
# 1:identity   <1 : shift slope up  >1 : shift slope down
slopeShiftExponent = params["shift"]

# this parameter is how many times we check the hachure spacing
# smaller number runs faster, but if lines are getting too close or too
# far, it's not checking often enough
# nombre de courbes (et non équidistance)
spacing_checks = params["checks"]

min_slope = params["minslope"]  # degrees
max_slope = params["maxslope"]

DEM = iface.activeLayer()  # The layer of interest must be selected
average_pixel_size = 0.5 * (DEM.rasterUnitsPerPixelX() + DEM.rasterUnitsPerPixelY())
jump_distance = average_pixel_size * 3
jump_distance_2 = (jump_distance*1.5) ** 2

if min_spacing is None:
    min_spacing = average_pixel_size * min_hachure_spacing
    max_spacing = average_pixel_size * max_hachure_spacing

spacing_range = max_spacing - min_spacing

TITLE = f"Hachures-{min_spacing:0.0f}-{max_spacing:0.0f}-{min_slope:0.0f}-{max_slope:0.0f}-{slopeShiftExponent:0.1f}"
SLOPE = 0
ASPECT = 1

min_slope = fcnExpScale(min_slope, 0, 90, 0, 90, slopeShiftExponent)
max_slope = fcnExpScale(max_slope, 0, 90, 0, 90, slopeShiftExponent)
slope_range = max_slope - min_slope


# ============================PREPATORY WORK=============================
tools.log("STEP 1 - Get slope/aspect/contours using built in tools")
# ---------STEP 1: Get slope/aspect/contours using built in tools--------
stats = DEM.dataProvider().bandStatistics(1)
elevation_range = stats.maximumValue - stats.minimumValue
contour_interval = elevation_range / spacing_checks


slope_layer = tools.getLayer("slope")
aspect_layer = tools.getLayer("aspect")
filled_contours = tools.getLayer("filled_contours")
line_contours = tools.getLayer("line_contours")

parameters = {"INPUT": DEM, "OUTPUT": "TEMPORARY_OUTPUT"}
if slope_layer is None:
    slope_layer = QgsRasterLayer(
        processing.run("qgis:slope", parameters)["OUTPUT"], "Slope"
    )
    tools.addMapLayer(slope_layer, name="slope", visible=False)

if aspect_layer is None:
    aspect_layer = QgsRasterLayer(
        processing.run("qgis:aspect", parameters)["OUTPUT"], "Aspect"
    )
    tools.addMapLayer(aspect_layer, name="aspect", visible=False)

parameters["INTERVAL"] = contour_interval
if filled_contours is None:
    filled_contours = QgsVectorLayer(
        processing.run("gdal:contour_polygon", parameters)["OUTPUT"],
        "Contour Layer",
        "ogr",
    )
    tools.addMapLayer(filled_contours, name="filled_contours", visible=False)

if line_contours is None:
    line_contours = QgsVectorLayer(
        processing.run("gdal:contour", parameters)["OUTPUT"], "Contour Layer", "ogr"
    )
    tools.addMapLayer(line_contours, name="line_contours", visible=False)


# --------STEP 2: Set up variables & prepare rasters for reading---------
tools.log("STEP 2: Set up variables & prepare rasters for reading")
instance = QgsProject.instance()
crs = instance.crs()

provider = slope_layer.dataProvider()
extent = provider.extent()
rows = slope_layer.height()
cols = slope_layer.width()
slope_block = provider.block(1, extent, cols, rows)

aspect_block = aspect_layer.dataProvider().block(1, extent, cols, rows)

cell_width = extent.width() / cols
cell_height = extent.height() / rows


# ===========================CLASS DEFINITIONS===========================
# ------Contour lines are used to check the spacing of the hachures------
class Contour:
    def __init__(self, contour_geometry, poly_geometry):
        self.geometry = contour_geometry
        self.polygon = poly_geometry

    def ring_list(self):
        # Returns a list of all rings that this contour is made from
        if self.geometry.isMultipart():
            all_rings = [
                QgsGeometry.fromPolylineXY(line)
                for line in self.geometry.asMultiPolyline()
            ]
        else:
            all_rings = [self.geometry]
        return all_rings

    def split_by_hachures(self):
        # Split this contour according to our current list of hachures
        all_segments = []

        for line_geometry in self.ring_list():

            intersection_points = []
            for hachure_geometry in current_hachures:
                point = line_geometry.intersection(hachure_geometry)
                if point.wkbType() == QgsWkbTypes.MultiPoint:
                    intersection_points += [
                        CutPoint(QgsGeometry.fromPointXY(p), hachure_geometry)
                        for p in point.asMultiPoint()
                    ]
                elif point.wkbType() == QgsWkbTypes.Point:
                    intersection_points += [CutPoint(point, hachure_geometry)]
                # The intersection can return Empty or (rarely)
                # a geometryCollection. We can safely skip over these

            for point in intersection_points:
                # This tells us where along the line to cut
                point.cut_location = line_geometry.lineLocatePoint(point.geometry)

            if len(intersection_points) > 0:
                # If we found intersections, use them to cut the ring
                contour_segments = cutpoint_splitter(line_geometry, intersection_points)
                all_segments += contour_segments
            else:
                # If not, we should still return the unbroken ring
                all_segments.append(Segment(line_geometry))

        return all_segments


# ----Segments are contour pieces used to space or generate hachures-----
class Segment:
    def __init__(self, geom):
        self.geometry = QgsGeometry(geom)
        self.length = self.geometry.length()
        self.slope = None
        self.hachures = []
        # Status stores info on how this segment should affect hachures
        # These values are used later in subsequent_contour
        self.status = None

    def getStatus(self):
        # The 0.9 and 2.2 above are thermostat controls. Instead of a
        # line being "too short" when it exactly falls below its ideal
        # spacing, we let it get a little tighter to avoid near-parallel
        # hachures cycling on/off rapidly.
        if self.status is None:
            try:
                if self.getSlope() < min_slope:
                    self.status = 0
                elif self.length < (ideal_spacing(self.getSlope()) * 0.9):
                    self.status = 1
                elif self.length > (ideal_spacing(self.getSlope()) * 2.2):
                    self.status = 2
            except Exception:
                self.status = 0

        return self.status

    def ring_list(self):
        return [self.geometry]

    def getSlope(self):
        if self.slope is None:
            # Get the average slope under this segment
            densified_line = self.geometry.densifyByDistance(average_pixel_size)
            row_col_coords = [xy_to_rc(vertex.x(), vertex.y()) for vertex in densified_line.vertices()]
            samples = [sample_raster(c, SLOPE) for c in row_col_coords]
            self.slope = statistics.fmean(samples)
        
        return self.slope


# --------------CutPoints mark where a contour is to be cut--------------
class CutPoint:
    def __init__(self, point_geometry, hachure_geom):
        self.geometry = point_geometry
        self.hachure = hachure_geom
        self.cut_location = None


# =========================FUNCTION DEFINITIONS-=========================
# --------Converts x/y coords to row/col for sampling the rasters--------
def xy_to_rc(x, y):
    col = round((x - extent.xMinimum()) / cell_width - 0.5)
    row = round((extent.yMaximum() - y) / cell_height - 0.5)

    return (row, col)


# -------------------Samples the slope or aspect raster------------------
def sample_raster(location, type=SLOPE):
    row, col = location

    if row >= rows or col >= cols or row < 0 or col < 0:
        # i.e., if we're out of bounds
        return 0

    if type == SLOPE:
        return slope_block.value(row, col)
    else:
        return aspect_block.value(row, col)


# -----------Given a slope, find the ideal spacing of hachures-----------
def ideal_spacing(slope):
    global slopeShiftExponent

    slope = fcnExpScale(slope, 0, 90, 0, 90, slopeShiftExponent)

    if slope > max_slope:
        slope = max_slope
    elif slope < min_slope:
        # None indicates that slope is too shallow & needs no hachures
        return None

    # Finds where the slop is in the range of min/max slope
    # Then normalizes it to the range of min/max spacing
    slope_pct = (slope - min_slope) / slope_range
    spacing_qty = slope_pct * spacing_range
    spacing = max_spacing - spacing_qty

    return spacing


# --Take Segments & turn them into dashed lines based on ideal spacing---
def dash_maker(contour_segment_list):

    output_segments = []

    for contour_segment in contour_segment_list:
        QApplication.processEvents()
        slope = contour_segment.getSlope()
        if slope < min_slope:
            continue

        spacing = ideal_spacing(slope)
        if spacing is None:
            continue

        # We tune the spacing value based on the segment length to ensure
        # an integer number of dashes. This is rather like the automatic
        # dash/gap spacing in Adobe Illustrator

        # Our goal here is to split a segment into dashes & gaps, thusly:
        #  ----    ----    ----    ----    ----    ----    ----
        # Each dash length = spacing, surrounded by gaps half that width
        # Thus one unit looks like this: |  ----  |

        total_length = spacing * 2  # the length of a gap + dash + gap
        total_units = round(contour_segment.length / total_length)

        if total_units == 0:
            # Just in case we round down to the point of having 0 dashes
            continue

        dash_gap_length = contour_segment.length / total_units

        dash_width = dash_gap_length / 2
        # half of our gap-dash-gap is the dash

        gap_width = dash_width / 2
        start_point = gap_width
        end_point = dash_width + gap_width

        gc = contour_segment.geometry.constGet()
        while True:
            line_substring = gc.curveSubstring(start_point, end_point)
            output_segments.append(Segment(line_substring))

            end_point += dash_gap_length

            if end_point > contour_segment.length:
                break

            start_point += dash_gap_length

    if len(output_segments) > 0:
        return output_segments
    else:
        return None


# -------------------Starts our first set of hachures--------------------
def first_contour(contour):
    global current_hachures

    # Split the contour into even segments to begin
    contour_segments = even_splitter(contour)

    # Then turn them into dashes
    dashes = dash_maker(contour_segments)

    if dashes:
        current_hachures = hachure_generator(dashes)


# ----Checks a contour to see where hachures need to be trimmed/begun----
def subsequent_contour(contour):
    global current_hachures

    # First we split the contour according to the existing hachures

    split_contour = contour.split_by_hachures()

    # We may need to further subdivide some of these. Some segments may
    # be too long & their slope calculations are no longer local

    segment_list = []

    for segment in split_contour:
        QApplication.processEvents()
        if segment.length > max_spacing * 3:
            segment_list += even_splitter(segment)
        else:
            segment_list += [segment]

    too_short = []
    too_long = []
    clip_all = []

    for segment in segment_list:
        if segment.getStatus() == 1:
            too_short.append(segment)
        elif segment.getStatus() == 2:
            too_long.append(segment)
        elif segment.getStatus() == 0:
            clip_all.append(segment)

    # too_short: this segment spans 2 hachures that are too close
    # too_long: segment's 2 hachures are too far apart
    # clip_all: this segment's slope is low enough that hachures stop

    # We first find which hachures must be clipped off

    to_clip = []

    for seg in clip_all:
        to_clip.extend(seg.hachures)

    for seg in too_short:
        hachures = seg.hachures
        if len(hachures) == 2:
            # Some segments won't touch enough hachures
            random.shuffle(hachures)
            to_clip.append(hachures[0])

    # to_clip can have duplicates. A hachure may have too_short segments
    # on each side, and both of them choose that particular hachure as
    # the 1 that needs to be clipped off. So we remove duplicates:

    to_clip = list(set(to_clip))

    # Remove those to be clipped from the current hachures
    current_hachures = [g for g in current_hachures if g not in to_clip]

    # Clip them, then put them back
    clipped_hachures = haircut(contour, to_clip)
    current_hachures += clipped_hachures

    # Let's next deal with adding new hachures to the too_long segments

    made_additions = False
    if len(too_long) > 0:
        dashes = dash_maker(too_long)

        if dashes:  # this could come back with None so we must check
            made_additions = True
            additions = hachure_generator(dashes)

    if made_additions:
        current_hachures += additions


# ----Clips off hachures that need to stop at this particular contour----
def haircut(contour, hachure_list):

    contour_poly_geometry = contour.polygon

    clipped = []
    for hachure_geo in hachure_list:
        clipped.append(hachure_geo.difference(contour_poly_geometry))

    return clipped


# --Generates new hachures starting at the middle of any given segment---
def hachure_generator(segment_list):

    # First we need the midpoint in each line, to begin our hachure from
    start_points = []

    for segment in segment_list:
        midpoint = segment.geometry.interpolate(segment.length / 2)
        start_points.append(midpoint.asPoint())

    # Next loop through the start_points & make hachures

    geom_list = []

    for coords in start_points:
        line_coords = [coords]

        x, y = coords
        rc = xy_to_rc(x, y)
        value = sample_raster(rc, ASPECT)  # 1= Get the aspect value

        if value == 0:  # if we go out of bounds, stop this line
            continue

        # And here I try to recall 11th-grade trigonometry

        value += 180
        new_x = x + math.sin(math.radians(value)) * jump_distance
        new_y = y + math.cos(math.radians(value)) * jump_distance

        line_coords += [(new_x, new_y)]

        for _ in range(0, 150):
            # this loop is a failsafe in case other checks below fail
            # to stop the hachure when they should

            x, y = line_coords[-1]
            rc = xy_to_rc(x, y)
            value = sample_raster(rc, ASPECT)  # get the aspect value
            if value == 0:  # we're out of bounds of the raster
                del line_coords[-1]
                break

            slope = sample_raster(rc, SLOPE)  # the slope, too
            if slope < min_slope:
                # if we hit shallow slopes, lines should end
                del line_coords[-1]
                break

            # Hachures often bounce back and forth in shallow slopes &
            # should stop. If lines are zig-zagging, every other point
            # will be separated by only a small distance

            if len(line_coords) > 3 and sqdist(line_coords[-1], line_coords[-3]) < jump_distance_2:
                # Snip off the last couple points if we've gone bad:
                del line_coords[-2:]
                break

            value += 180
            new_x = x + math.sin(math.radians(value)) * jump_distance
            new_y = y + math.cos(math.radians(value)) * jump_distance
            line_coords += [(new_x, new_y)]

        if len(line_coords) > 1:
            # if we stopped before we even got 2 points, don't bother
            geom_list.append(make_lines(line_coords))

    return geom_list


# ---------------------Cartesian square distance calculator---------------------
def sqdist(one, two):
    x1, y1 = one
    x2, y2 = two
    return (x1 - x2) ** 2 + (y1 - y2) ** 2

# -------Turns list of tuples of xy coodinates into a line Geom-------
def make_lines(coord_list):
    points = [QgsPointXY(x, y) for x, y in coord_list]
    polyline = QgsGeometry.fromPolylineXY(points)

    return polyline

# -----Splits a line  into even segments based on max_spacing-----
def even_splitter(contourOrSeg):
    spacing = max_spacing * 3
    output_segments = []

    for line_geometry in contourOrSeg.ring_list():
        length = line_geometry.length()
        # start_point = 0
        # end_point = spacing

        i = spacing
        cut_locations = []
        while i < length:
            cut_locations.append(i)
            i += spacing

        output_segments.extend(master_splitter(line_geometry, cut_locations))

    return output_segments


# ---Takes a single line geometry and splits it at a list of locations---
def master_splitter(line_geometry, cut_locations):
    start_point = 0
    cut_locations.append(line_geometry.length())
    cut_locations.sort()

    segment_list = []

    constline = line_geometry.constGet()
    for cut_spot in cut_locations:
        line_substring = constline.curveSubstring(start_point, cut_spot)
        segment_list.append(Segment(line_substring))
        start_point = cut_spot

    return segment_list


# ---Like master_splitter, but uses CutPoints instead of cut locations---
def cutpoint_splitter(line_geometry, CutPoint_list):
    CutPoint_list.sort(key=lambda x: x.cut_location)

    # CutPoints hold info on what hachure generated them; we want to add
    # that info to the subsequent segments

    segment_list = []

    # Add first segment
    constline = line_geometry.constGet()
    line_substring = constline.curveSubstring(
        0, CutPoint_list[0].cut_location
    )
    segment_list.append(Segment(line_substring))

    # Then do all the middle cuts & append hachure data to the Segments
    for i in range(0, len(CutPoint_list)):
        start_point = CutPoint_list[i]
        start_location = start_point.cut_location
        if i == len(CutPoint_list) - 1:
            # Checks if we're at end of the list & handles final segment
            end_location = line_geometry.length()
        else:
            end_point = CutPoint_list[i + 1]
            end_location = end_point.cut_location

        line_substring = constline.curveSubstring(
            start_location, end_location
        )
        new_segment = Segment(line_substring)
        segment_list.append(new_segment)
        if i != len(CutPoint_list) - 1:
            new_segment.hachures = [start_point.hachure, end_point.hachure]

    return segment_list


# ===============FUNCTIONS OVER; BEGIN CONTOUR PREPARATION===============
# -STEP 1: Process the contours so that they are all in the needed format
tools.log("STEP 1: Process the contours")

# instance.addMapLayer(filled_contours,False)
# Add filled_contours as hidden layer so I can work with it below

# First we sort the contours from low elevation to high.
# They probably were already sorted this way, but let's not chance it.

contour_polys = [f for f in filled_contours.getFeatures()]
contour_polys.sort(key=lambda x: x.attributeMap()["ELEV_MIN"])

# Each contour poly will be turned into a new polygon showing all areas
# that are *higher* than that contour

# -----STEP 2: Make a simple rectangle poly covering contours' extent----
tools.log("STEP 2: Make a simple rectangle")
extent = filled_contours.extent()
boundary_polygon = QgsGeometry.fromRect(extent)

# --STEP 3: Iterate through each contour poly and subtract it from our---
tools.log("STEP 3: Iterate")
# ------rectangle, thus yielding rectangles with varying size holes------

contour_geometries = [f.geometry() for f in contour_polys]

# Loop below starts with our boundary rectangle, subtracts the lowest
# elevation poly from it, and stores the result. It then subtracts the
# 2nd-lowest poly from that result and stores that. And so on, each time
# subtracting the next-lowest poly from the result of the last operation

working_geometry = boundary_polygon
contour_differences = []

for geom in contour_geometries[:-1]:
    # We drop the last one because it's going to be empty
    working_geometry = working_geometry.difference(geom)
    contour_differences.append(working_geometry)

# ------------------STEP 4: Dissolve the contour lines-------------------
tools.log("STEP 4: Dissolve")
contour_dict = defaultdict(list)

for feature in line_contours.getFeatures():
    contour_dict[feature.attributeMap()["ELEV"]].append(feature)
    # this dict is now of the form {Elevation: [list of features]}

keys = list(contour_dict.keys())
keys.sort()

# we need to sort these low-to-high so they match the order of the
# contour_differences we just generated

dissolved_lines = []
for key in keys:
    geometries = [f.geometry() for f in contour_dict[key]]
    combined_geo = QgsGeometry.collectGeometry(geometries)
    dissolved_lines.append(combined_geo)

# then turn them into Contours for use by the main loop
contour_lines = []
for dissolved_line, poly_geometry in zip(dissolved_lines, contour_differences):
    contour_lines.append(Contour(dissolved_line, poly_geometry))

# each Contour carrys a record of its corresponding poly for use by haircut

# instance.removeMapLayer(filled_contours) # no longer needed

# ========MAIN LOOP: Iterate through Contours to generate hachures=======
tools.log("MAIN LOOP 1 : Iterate through Contours")

current_hachures = None

# As we iterate through, it's possible that it takes a few contour lines
# before the slope is high enough (i.e. > min_slope) to make hachures.
# So each time, the if statement checks to see if we got anything back.
# Otherwise it moves to the next line and again tries to generate
# a set of starting hachures.
t0 = datetime.now()
okToContinue = False


def progressLogAndContinueOrNot(i, tot, limit=300):
    global t0, okToContinue

    QApplication.processEvents()

    t1 = datetime.now()
    dt = t1 - t0
    if dt.total_seconds() > 5 and not okToContinue:
        reste = (tot - i) * (dt / (i + 1))
        if reste.total_seconds() > limit:
            d = reste.total_seconds()
            r = QMessageBox.question(
                None,
                f"Traitement long ({d:.0f} s)",
                "Continuer ?",
                QMessageBox.Yes | QMessageBox.No,
            )
            okToContinue = r == QMessageBox.Yes
            if not okToContinue:
                return False

    d = (tot - i) * (dt / (i + 1))
    d = d.total_seconds()
    tools.log("{}/{} reste {:.0f}s".format(i, tot, d), delay=5)

    return True


fc = len(contour_lines)
for i, line in enumerate(contour_lines):
    if not progressLogAndContinueOrNot(i, fc, limit=300):
        break

    if current_hachures:
        subsequent_contour(line)
    else:
        first_contour(line)

# We sometimes pick up errant duplicates, so let's clean the final list
current_hachures = list(set(current_hachures))

# Add it to the map & also add length attributes so user can filter
hachureLayer = QgsVectorLayer("linestring", "Hachures", "memory")
hachureLayer.setCrs(crs)

with edit(hachureLayer):
    feats = []
    for g in current_hachures:
        newf = QgsFeature()
        newf.setGeometry(g)
        feats.append(newf)
    hachureLayer.dataProvider().addFeatures(feats)

r = processing.run(
    "native:setzfromraster",
    {
        "INPUT": hachureLayer,
        "RASTER": DEM,
        "BAND": 1,
        "NODATA": 0,
        "SCALE": 1,
        "OFFSET": 0,
        "OUTPUT": "TEMPORARY_OUTPUT",
    },
)

hachureLayer = r["OUTPUT"]
hachureLayer.setName("Hachures")
hachureLayer.setTitle(TITLE)

instance.addMapLayer(hachureLayer)

tools.log("FIN !!")

