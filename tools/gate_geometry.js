/* In-page geometry gate: every vertex, against the REAL polygon.
 *
 * Why this is not a static check: you cannot know a vertex position by reading
 * source. Positions come out of loops, from ctx.ground(), from shared kit
 * components. One project's "static estimate" (counting b.box( occurrences)
 * came out 16x low. A fake number is worse than no number.
 *
 * So: drive the actual page, then walk the actual BufferGeometry.
 *
 * USAGE — two ways.
 *
 *   1) Paste-in (no build step):
 *        <script src="gate_geometry.js"></script>
 *      then in the console:
 *        await GateGeometry.run()
 *
 *   2) Automated (Playwright / Puppeteer):
 *        await page.evaluate(() => GateGeometry.run())
 *
 * REQUIRES the page to expose, after it has built the unit:
 *
 *   window.__UNIT_RESULT__ = {
 *     group:  THREE.Group,   // the built unit
 *     marks:  [...],         // optional: [{name, tris}]
 *   };
 *   window.__PREVIEW_READY__ = true;
 *
 *   and window.__UNIT_POLYGON__ = [[x,z], ...]  (the unit's true outline)
 *
 * ADAPT: the two config lines below. Everything else is portable.
 */

(function (global) {
  'use strict';

  /* ------------------------------------------------------------------ config */
  var CFG = {
    // how far outside the polygon a vertex may sit before it counts as a
    // violation. A bench 6 cm over the line is noise; 40 cm is a bug.
    slack: 1.6,

    // the global the page hangs its result on
    resultGlobal: '__UNIT_RESULT__',

    // set false to include helper objects (grid, axes, lights) — almost always
    // you want them skipped, they are not the unit's fault
    skipHelperNames: ['grid', 'axes', 'helper', 'light', 'camera', 'sky', 'fog']
  };

  /* ------------------------------------------------------- point in polygon */
  /* Even-odd ray cast. Same algorithm as the Python half, deliberately: if the
     two implementations disagree you get a check that contradicts itself. */
  function pointInPoly(x, z, poly) {
    var inside = false, n = poly.length, j = n - 1;
    for (var i = 0; i < n; i++) {
      var xi = poly[i][0], zi = poly[i][1];
      var xj = poly[j][0], zj = poly[j][1];
      if ((zi > z) !== (zj > z)) {
        var xint = (xj - xi) * (z - zi) / (zj - zi + 1e-12) + xi;
        if (x < xint) inside = !inside;
      }
      j = i;
    }
    return inside;
  }

  /* Distance from a point to the polygon boundary, in the XZ plane.
     We allow slack OUTSIDE the polygon too — see CFG.slack. Returning the
     distance lets the report say "40 cm out" instead of just "1 bad". */
  function distToBoundary(x, z, poly) {
    var best = Infinity, n = poly.length;
    for (var i = 0, j = n - 1; i < n; j = i++) {
      var ax = poly[j][0], az = poly[j][1];
      var bx = poly[i][0], bz = poly[i][1];
      var dx = bx - ax, dz = bz - az;
      var len2 = dx * dx + dz * dz;
      var t = len2 > 0 ? ((x - ax) * dx + (z - az) * dz) / len2 : 0;
      t = t < 0 ? 0 : (t > 1 ? 1 : t);
      var px = ax + t * dx, pz = az + t * dz;
      var d = Math.hypot(x - px, z - pz);
      if (d < best) best = d;
    }
    return best;
  }

  /* ------------------------------------------------------------ helpers */
  function isHelper(obj) {
    if (!obj || !obj.name) return false;
    var n = obj.name.toLowerCase();
    for (var i = 0; i < CFG.skipHelperNames.length; i++) {
      if (n.indexOf(CFG.skipHelperNames[i]) >= 0) return true;
    }
    return false;
  }

  /* Count triangles by walking the scene graph. Geometry is often SHARED
     between many meshes (that is the point of merging), so we sum
     index.count/3 per mesh, not per unique geometry. */
  function countTriangles(root) {
    var tris = 0, meshes = 0;
    root.traverse(function (o) {
      if (!o.isMesh || isHelper(o)) return;
      var g = o.geometry;
      if (!g) return;
      var per = g.index ? g.index.count : (g.attributes.position ? g.attributes.position.count : 0);
      tris += per / 3;
      meshes++;
    });
    return { tris: Math.round(tris), meshes: meshes };
  }

  /* ------------------------------------------------------------- the check */
  /* Returns {
       ok, vertices, outOfBounds, worst, samples[], tris, meshes, marks
     }
     Out of bounds = not inside the polygon AND farther than CFG.slack from its
     boundary. We check the WORLD position, so a unit that forgot to add ctx.x
     is caught here even though its local numbers look fine. */
  function measure() {
    var res = global[CFG.resultGlobal];
    if (!res || !res.group) {
      return { ok: false, error: 'no ' + CFG.resultGlobal + '.group — 页面没构建完，或没暴露结果' };
    }
    var poly = global.__UNIT_POLYGON__;
    if (!poly || poly.length < 3) {
      return { ok: false, error: 'no __UNIT_POLYGON__ — 没有真实多边形就无法验边界' };
    }

    var v3 = new (global.THREE ? global.THREE.Vector3 : Object)();
    var vertices = 0, out = 0, worst = 0, samples = [];

    res.group.updateWorldMatrix(true, true);

    res.group.traverse(function (o) {
      if (!o.isMesh || isHelper(o)) return;
      var g = o.geometry;
      if (!g || !g.attributes || !g.attributes.position) return;
      var pos = g.attributes.position;

      for (var i = 0; i < pos.count; i++) {
        if (global.THREE) {
          v3.fromBufferAttribute(pos, i).applyMatrix4(o.matrixWorld);
        } else {
          v3.x = pos.getX(i); v3.y = pos.getY(i); v3.z = pos.getZ(i);
        }
        vertices++;

        var x = v3.x, z = v3.z;
        if (pointInPoly(x, z, poly)) continue;

        var d = distToBoundary(x, z, poly);
        if (d <= CFG.slack) continue;          // within tolerated margin

        out++;
        if (d > worst) worst = d;
        if (samples.length < 8) {
          samples.push({ x: +x.toFixed(2), z: +z.toFixed(2), out: +d.toFixed(2) });
        }
      }
    });

    var tc = countTriangles(res.group);
    return {
      ok: out === 0,
      vertices: vertices,
      outOfBounds: out,
      worst: +worst.toFixed(2),
      slack: CFG.slack,
      samples: samples,
      tris: tc.tris,
      meshes: tc.meshes,
      marks: (res.marks || []).map(function (m) { return m && m.name; }).filter(Boolean)
    };
  }

  /* The silent-empty-section check, client half.
     Compare what the SOURCE declared with what actually got built. You have to
     pass the declared list in, because source text is not available here. */
  function emptySections(declared) {
    var res = global[CFG.resultGlobal];
    var built = {};
    ((res && res.marks) || []).forEach(function (m) { if (m && m.name) built[m.name] = 1; });
    return (declared || []).filter(function (n) { return !built[n]; });
  }

  /* Ancillary: frame time. Cap awareness — a number below ~20 fps is more often
     a software rasteriser than a heavy mesh. Confirm the GPU first. */
  function frameTime(frames) {
    frames = frames || 60;
    return new Promise(function (resolve) {
      var t = [], last = performance.now(), n = 0;
      function tick() {
        var now = performance.now();
        t.push(now - last); last = now;
        if (++n < frames) requestAnimationFrame(tick);
        else {
          t.sort(function (a, b) { return a - b; });
          var med = t[Math.floor(t.length / 2)];
          resolve({ medianMs: +med.toFixed(2), fps: +(1000 / med).toFixed(1) });
        }
      }
      requestAnimationFrame(tick);
    });
  }

  /* ------------------------------------------------------------- reporting */
  function report(r) {
    if (r.error) { console.error('[gate] ' + r.error); return r; }
    var tag = r.ok ? 'PASS' : 'FAIL';
    console.log('[gate] ' + tag + '  顶点 ' + r.vertices +
      '  出界 ' + r.outOfBounds + (r.outOfBounds ? ('（最远 ' + r.worst + ' m，容差 ' + r.slack + ' m）') : '') +
      '  三角 ' + r.tris + '  网格 ' + r.meshes);
    if (r.samples.length) {
      console.log('[gate] 越界样本：');
      r.samples.forEach(function (s) {
        console.log('        x=' + s.x + '  z=' + s.z + '  超出 ' + s.out + ' m');
      });
    }
    /* Also hand the numbers to an automated runner. A gate that can only be
       read by a human is a gate nobody runs on every batch. */
    global.__GATE_RESULT__ = r;
    return r;
  }

  async function run(opts) {
    if (opts && typeof opts.slack === 'number') CFG.slack = opts.slack;
    return report(measure());
  }

  global.GateGeometry = {
    run: run,
    measure: measure,
    emptySections: emptySections,
    frameTime: frameTime,
    pointInPoly: pointInPoly,
    distToBoundary: distToBoundary,
    countTriangles: countTriangles,
    CFG: CFG
  };
})(typeof window !== 'undefined' ? window : globalThis);
