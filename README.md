# Hachures
A QGIS method to generate automated hachure lines. Like these:

<img width="486" alt="image" src="https://github.com/pinakographos/Hachures/assets/5448396/278f4127-dfae-443a-93b3-82075ea807b8">

# Preamble
**This is a work in progress.** I'm sure bugs & inefficiencies will be found. Meanwhile, the sample DEM provided works successfully for me, and the process can be run in a couple of minutes on it using the default settings in the scripts and the example DEM. While this repo is public, I'm sharing the script in a limited fashion right now to get early feedback. Once it's more ready, I'll launch it more fully into the cartographic community.

Known bug: this generates various memory layers that you won't see in the table of contents, but which will show up on the dropdown menu for various QGIS tools.

Thanks to Nyall Dawson for some significant efficiency gains!

# Walkthrough

Let's dive into a high-level review of how all this works. My method, built up organically over weeks of trial and error, is sometimes inelegant on account of the nature of its creation process, but it is effective. It is my hope that it will be a platform upon which others (perhaps including me) will build improved methods using fresh ideas.

## Initial Parameters
The user must select a DEM raster layer (`iface.activeLayer()`). They should also fill in a few parameters:

+ `contourInterval`. This script generates contour lines, and checks our hachure lines (more on that below) every contour.
+ `minSpacing` and `maxSpacing` specify, in map units, how close or how far apart we'd like our hachures to be.
+ `slopeMin` and `slopeMax` specify what slope levels we'll consider in making those hachures. The script makes hachures more dense when the slope of the terrain is higher, and spaces them out farther on shallower terrain. The closer a slope gets toward `slopeMax`, the denser the hachures will be, up to `minSpacing`. If terrain has a slope that is less than `slopeMin`, no hachures will be drawn in that area. If it has a slope equal to or greater than `slopeMax`, hachures will be at maximum density (spaced according to `minSpacing`).

## Generate Raster Derivaties
First off, we take our DEM and generate slope and aspect rasters, as well as a contour polygon layer, using QGIS's existing processes.
The `contourInterval` parameter sets the contour interval, in the DEM's Z units.
<img width="1472" alt="image" src="https://github.com/pinakographos/Hachures/assets/5448396/3bcf2980-1fd8-4a3e-acb5-ff8396d34b23">

## Contour Poly Reformatting
The hachure script presently requires that the contours be in a particular format. Initially, the contour polys that we generated show all elevations between two specific values. 
<img width="1331" alt="image" src="https://github.com/pinakographos/Hachures/assets/5448396/6018a802-2fc9-4de3-9904-d82edf11f7ed">

However, for the hachure process to work, reformatting is needed. For each contour level, we need to generate a polygon that shows all areas that are **higher** than that elevation.
<img width="1307" alt="image" src="https://github.com/pinakographos/Hachures/assets/5448396/d4517c19-50e9-4044-b603-3ca877206e49">

This is done in the script by creating a layer with a simple rectangle that matches the bounds of the contour layer. For each contour polygon, we subtract it, and all other contours lower than it, from the a copy of the rectangle poly, using the `difference` processing tool. This yields the result we want, as seen in the example above. Finally, we convert those polygons to lines. This yields what the hachure tool requires: closed contours, where the inside of each closure represents all elevations above that contour's value.

Now we are ready to begin hachure generation through the main loop of the script.

## Contour setup
The script iterates through the contour lines, starting with the lowest-elevation one. Based on how we generated these, this will be a closed loop (or set of closed loops) that, in polygon form, cover all areas **higher** than our contour's elevation.

We begin by dividing this contour line into chunks with the QGIS tool `splitlinesbylength`. Each chunk is `maxSpacing * 3` in width. This choice is somewhat arbitrary on my part, but the results seem to work pleasantly enough.

<img width="705" alt="image" src="https://github.com/pinakographos/Hachures/assets/5448396/b858a338-496d-400a-bed0-c5ae46d8c232">

The script then assigns a few attributes to each split, including an ID number, its length, and most importantly, its average **slope**. Each split chunk is densified with extra vertices (spaced according to the pixel size of the slope raster), and then we use each vertex to sample the slope raster, and average it. So now we know the average slope covered by each split.

## Contour Splitting
Using this slope information, along with the user parameters, we can determine how many hachures should pass through the zone covered by this particular chunk of a contour line. Its average slope is compared to the `slopeMax` and `slopeMin`, and we use the `minSpacing` and `maxSpacing` parameters to determine how dense the hachures should be here. Let's say that we have the following parameters:
+ `minSpacing = 2`
+ `maxSpacing = 10`
+ `slopeMin = 10`
+ `slopeMax = 45`

And let's say our chunk of contour line has an average slope of 35°. That slope of 35° is about 71% of the way from 10° to 45° ((35 - 10) / (45 - 10)). We take that percentage back to our spacing parameters and find the spacing that is 71% of the way between 2 and 10. And here, denser spacing = more slope, so we want the value that is closer to 2 than 10. We get a value of 10 - ((10 - 2) * 0.71) = 4.3. This is our final `spacing` value for our example chunk.

We take that chunk of contour and split it into a series of dashes and gaps, each 4.3 map units in length. We repeat this process for each of the chunks of contours, until each is split into dashes and gaps, and the size of those dashes/gaps varies according to our underlying slope and our user parameters of how much min/max spacing we want. If a chunk's slope is less than `slopeMin`, we eliminate it.

<img width="735" alt="image" src="https://github.com/pinakographos/Hachures/assets/5448396/d9331072-a77f-44a3-b118-d900771cd230">

This is why I initially split the line into chunks that are `maxSpacing * 3` long. It makes them wide enough that there will be room for a few dashes/gaps, while keeping it small enough to also make sure that it reflects a **local** slope value

These dashes/gaps (which are adjusted a bit in length based on the length of the actual contour chunk) are used to generate hachures.

## Hachure Generation
To begin, we generate a point at the center of each dash. We use that as a starting point for drawing each of our initial set of hachure lines. We look at the aspect raster that was generated earlier, and use this to determine which direction to run these hachure lines (up/down the slope) using some trigonometry (and luck on my part, as far as remembering how trigonometry works). The trig functions tell us what direction the line should run. We jump 3 pixels in that direction and then sample the aspect raster again, then jump another 2 pixels along, etc. 

<img width="1336" alt="image" src="https://github.com/pinakographos/Hachures/assets/5448396/3c2d7ddc-afac-44db-b70b-5025214e96c1">

The line stops when it hits a shallow slope, or starts to bounce around a sink, or (as a failsafe) when it reaches 150 points long. Else we'll get things like this:

![image](https://github.com/pinakographos/Hachures/assets/5448396/3e92fa2e-0b94-41b5-b371-0f245f29c945)

This hachure generation setup is very akin to some hydrological modelling. I originally experimented with using a flow direction raster, in which each pixel specifies which of its 8 neighboring pixels water would flow into if headed downhill. But, with only 8 directions to choose from, the results were rather jagged, vs. the aspect raster which can have any angle value to specify our next direction (which we take advantage of by skipping a couple pixels over before sampling again). It may still be worth exploring someday — perhaps generating a flow raster as a standalone internal feature in the script, and smoothing out the jagged lines afterwards.

We then store our current set of hachures and move on to the next contour line in the sequence.

Before moving on, I want to note that the reason for starting hachures at these dashes is that making a set of dashes and gaps is used to enforce a **minimum** spacing between hachures. The gaps and dashes ensure that they cannot get too close.

## Continuing upwards

We now move on to the **next** contour line (the second-lowest one). For this line, and any subsequent ones, there are a couple of small changes to the procedure.

For the next contour, we not only split it by length (again, `maxSpacing * 3`), but we also split it based on its intersections with the hachures retained from the prior contour layer(s).

<img width="1340" alt="image" src="https://github.com/pinakographos/Hachures/assets/5448396/5f86214d-06e5-4942-bdb0-c32733e43a83">

Then we look through each split, get its average slope once again, and once again run the `spacing` calculation to determine how far apart hachures should be in this area based on the underlying slope. This time we take some extra steps. Many of the contour splits seen above are touched on each end by a hachure; remember the contours were divided by the hachures (and by `maxSpacing`). So, the length of that contour split encodes how close together the hachures are. If a contour's ideal spacing (based on the underling slope) is larger than its length, that means that the two hachures that touch it are **too close together** now according to the underlying slope. We should trim at least one of them off. We look at both hachures and determine which is the longest, and keep that one and stop the other one here, at this contour. I have prioritized the longest hachures for continuity. But it would probably look fine if the shortest hachures or random ones were kept instead.

If the local slope is below `slopeMin`, we cut off both as this is an area with a gradual enough slope that no hachures should be shown. Here we can see in the middle how one line got cut off:

<img width="508" alt="image" src="https://github.com/pinakographos/Hachures/assets/5448396/5b93119a-b06e-4b66-900f-9dff1cb81f50">

It started at the outer contour and when it was checked against the next contour on the inside, that slope was too shallow and it was time to cut the line off, while some other lines nearby continued.

The script can also determine which contour chunks are **too long**. If a chunk touches 2 hachures and is very long, longer than its preferred spacing, it means those hachures have drifted too far apart for their current slope. We need to start at least 1 new line along this segment. This is done much as it is above: we split that chunk into dashes based on its slope, and then begin new hachures at the center of each dash.

Finally, some contour chunks may not touch any hachures, in which case we treat them as normal and split them into varying dash lengths based on the slope, and then give them a line running through each dash.

Iterating through the entire set of contours, we get a set of hachures. They get clipped off as they get too close, and new lines begin again as they drift apart. Their spacing is controlled by the underlying slope.

<img width="486" alt="image" src="https://github.com/pinakographos/Hachures/assets/5448396/278f4127-dfae-443a-93b3-82075ea807b8">


### Final Thoughts 
A denser `contourInterval` means lines are trimmed/begun more often, because we check their spacing at each contour. This comes at a cost of more computation time, though. Irregular contour intervals would work here, too; it's not important that the contours be evenly spaced.
Near the edges of a DEM, you might get some odd lines. I recommend generating hachures on a slightly larger area than you need them. I also sometimes filter out the shortest stub lines for a more visually pleasing result.

Getting a good result takes time, and the script can run for several minutes or even hours, depending on the terrain size, and user parameters specified. I am working to make it more efficient, but I counsel patience in running this tool. The example DEM can be processed into hachures in about 1½ on my particular computer, with the default user settings specified in the script. 
