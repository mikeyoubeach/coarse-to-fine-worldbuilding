globalThis.window = undefined;
await import('./gate_geometry.js');
const G = globalThis.GateGeometry;
const poly = [[0, 0], [10, 0], [10, 10], [0, 10]];
console.log('in(5,5)', G.pointInPoly(5, 5, poly));
console.log('in(11,5)', G.pointInPoly(11, 5, poly));
console.log('dist(11,5)', G.distToBoundary(11, 5, poly).toFixed(3));
console.log('dist(-3,5)', G.distToBoundary(-3, 5, poly).toFixed(3));
G.CFG.slack = 1.6;
const mk = (verts) => ({
  group: {
    updateWorldMatrix() { },
    traverse(cb) {
      cb({
        isMesh: true, name: '', matrixWorld: {},
        geometry: {
          attributes: { position: { count: verts.length, getX: i => verts[i][0], getY: i => verts[i][1], getZ: i => verts[i][2] } },
          index: null
        }
      });
    }
  }
});
globalThis.__UNIT_POLYGON__ = poly;
globalThis.__UNIT_RESULT__ = mk([[5, 0, 5], [11, 0, 5], [12, 0, 5], [2, 0, 2]]);
G.run();
globalThis.__UNIT_RESULT__ = mk([[5, 0, 5], [10.5, 0, 5]]);
G.run();
console.log('emptySections', G.emptySections(['a', 'b']));
