// Rend les mêmes trajectoires dans Three.js ; l’import map localise les modules pour l’autonomie hors ligne.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
const A = window.apercu,
  D = A.D,
  box = document.getElementById("vue3d"),
  $ = (i) => document.getElementById(i);
let ren, scene, cam, ctl, G = null, actif = false;
const b64 = (s, T) => {
  const b = atob(s), u = new Uint8Array(b.length);
  for (let i = 0; i < b.length; i++) u[i] = b.charCodeAt(i);
  return new T(u.buffer);
};
const vit = (v) => {
  const t = Math.min(1, v / 300);
  return new THREE.Color().setHSL(.66 - .66 * t, .9, .5);
};
function init() {
  ren = new THREE.WebGLRenderer({ antialias: true });
  ren.setPixelRatio(Math.min(devicePixelRatio, 2));
  box.appendChild(ren.domElement);
  scene = new THREE.Scene();
  scene.background = new THREE.Color("#14161c");
  cam = new THREE.PerspectiveCamera(40, 1, .5, 5000);
  ctl = new OrbitControls(cam, ren.domElement);
  scene.add(new THREE.AmbientLight(0xffffff, .7));
  const d = new THREE.DirectionalLight(0xffffff, 1.2);
  d.position.set(1, 2, 1.5);
  scene.add(d);
  addEventListener("resize", taille);
  (function boucle() {
    requestAnimationFrame(boucle);
    if (actif) {
      ctl.update();
      ren.render(scene, cam);
    }
  })();
}
function taille() {
  if (!ren) return;
  const w = box.clientWidth, h = box.clientHeight;
  ren.setSize(w, h);
  cam.aspect = w / h;
  cam.updateProjectionMatrix();
}
// G-code (x, y, z) → three (x, z, −y) : Z du G-code vers le haut.
function construire() {
  if (G) {
    scene.remove(G.groupe);
    G.groupe.traverse((o) => {
      o.geometry && o.geometry.dispose();
    });
  }
  const C = D.plaques[A.plaqueIndex].couches, q = D.q, mode = $("mode").value;
  let n = 0, nt = 0;
  const dec = C.map((c) => {
    const d = b64(c.d, Int16Array),
      v = b64(c.v, Uint8Array),
      tr = b64(c.tr, Int16Array);
    let i = 0, s = 0;
    while (i < d.length) {
      s += d[i + 1];
      i += 4 + 2 * d[i + 1];
    }
    n += s;
    nt += tr.length / 5;
    return { d, v, tr, s };
  });
  const pos = new Float32Array(n * 6),
    col = new Float32Array(n * 6),
    deb = [],
    tp = new Float32Array(nt * 6),
    tc = new Float32Array(nt * 6),
    tdeb = [];
  let k = 0, kt = 0, vmax = 1;
  C.forEach((c, ci) => {
    deb.push(k);
    tdeb.push(kt);
    const { d, v, tr } = dec[ci], z = c.z;
    let i = 0, p = 0;
    while (i < d.length) {
      const ty = d[i], m = d[i + 1], sp = v[p++] || 0;
      vmax = Math.max(vmax, sp);
      const co = mode === "type" ? new THREE.Color(D.couleurs[ty]) : vit(sp);
      for (let j = 0; j < m; j++) {
        const a = i + 2 + 2 * j;
        pos.set(
          [d[a] / q, z, -d[a + 1] / q, d[a + 2] / q, z, -d[a + 3] / q],
          k * 6,
        );
        col.set([co.r, co.g, co.b, co.r, co.g, co.b], k * 6);
        k++;
      }
      i += 4 + 2 * m;
    }
    for (let j = 0; j < tr.length; j += 5) {
      const x0 = tr[j] / q,
        y0 = tr[j + 1] / q,
        x1 = tr[j + 2] / q,
        y1 = tr[j + 3] / q,
        le = tr[j + 4] / q,
        L = Math.hypot(x1 - x0, y1 - y0),
        r = L >= 40 && le < 1;
      tp.set([x0, z + le, -y0, x1, z + le, -y1], kt * 6);
      const co = r ? [1, .2, .2] : [.45, .45, .5];
      tc.set([...co, ...co], kt * 6);
      kt++;
    }
  });
  deb.push(k);
  tdeb.push(kt);
  const groupe = new THREE.Group();
  const g1 = new THREE.BufferGeometry();
  g1.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  g1.setAttribute("color", new THREE.BufferAttribute(col, 3));
  const lignes = new THREE.LineSegments(
    g1,
    new THREE.LineBasicMaterial({ vertexColors: true }),
  );
  const g2 = new THREE.BufferGeometry();
  g2.setAttribute("position", new THREE.BufferAttribute(tp, 3));
  g2.setAttribute("color", new THREE.BufferAttribute(tc, 3));
  const trajets = new THREE.LineSegments(
    g2,
    new THREE.LineBasicMaterial({
      vertexColors: true,
      transparent: true,
      opacity: .8,
    }),
  );
  groupe.add(lignes, trajets);
  const al = new THREE.Group();
  groupe.add(al);
  g1.computeBoundingBox();
  const bb = g1.boundingBox;
  for (const a of D.plaques[A.plaqueIndex].alertes || []) {
    const b = a.bbox || [bb.min.x, -bb.max.z, bb.max.x, -bb.min.z],
      h = Math.max(.2, a.z[1] - a.z[0]),
      geo = new THREE.BoxGeometry(
        Math.max(1, b[2] - b[0]),
        h,
        Math.max(1, b[3] - b[1]),
      );
    const m = new THREE.LineSegments(
      new THREE.EdgesGeometry(geo),
      new THREE.LineBasicMaterial({ color: a.bbox ? 0xffb347 : 0xff3bd2 }),
    );
    m.position.set(
      (b[0] + b[2]) / 2,
      (a.z[0] + a.z[1]) / 2,
      -(b[1] + b[3]) / 2,
    );
    al.add(m);
  }
  for (const f of D.plaques[A.plaqueIndex].defauts || []) {
    const m = new THREE.Mesh(
      new THREE.SphereGeometry(1.5, 12, 8),
      new THREE.MeshBasicMaterial({ color: 0xff3b3b }),
    );
    m.position.set(f.xy[0], C[0].z, -f.xy[1]);
    al.add(m);
  }
  const rub = new THREE.InstancedMesh(
    new THREE.BoxGeometry(1, 1, 1),
    new THREE.MeshLambertMaterial(),
    60000,
  );
  rub.count = 0;
  groupe.add(rub);
  scene.add(groupe);
  G = { groupe, lignes, trajets, al, rub, deb, tdeb, pos, col, C, vmax, bb };
  $("legv").textContent = mode === "vitesse"
    ? "Vitesse : bleu lent → rouge ≥ 300 mm/s (max de la plaque " + vmax +
      " mm/s)"
    : "";
  const ls = D.plaques[A.plaqueIndex].alertes || [];
  $("als").innerHTML = ls.length
    ? '<p class="doux">Alertes des contrôles (clic : aller à la couche)</p>' +
      ls.map((a) => '<div class="al">● ' + echapper(a.texte) + "</div>").join(
        "",
      )
    : '<p class="doux">Aucune alerte de contrôle fournie.</p>';
  document.querySelectorAll(".al").forEach((e, i) =>
    e.onclick = () => {
      const a = ls[i], ci = C.findIndex((c) => c.z >= a.z[1] - 1e-6);
      A.aller(ci < 0 ? C.length - 1 : ci);
      if (a.bbox) {
        ctl.target.set(
          (a.bbox[0] + a.bbox[2]) / 2,
          a.z[0],
          -(a.bbox[1] + a.bbox[3]) / 2,
        );
      }
    }
  );
  $("bas").max = C.length - 1;
  cadrer();
  maj();
}
function cadrer() {
  const b = G.bb, c = new THREE.Vector3();
  b.getCenter(c);
  const r = b.getSize(new THREE.Vector3()).length();
  ctl.target.copy(
    c,
  ); /* 27/10/2026 : distance selon le plus petit angle de vue (une vue étroite coupait la plaque) */
  const v = THREE.MathUtils.degToRad(cam.fov),
    h = 2 * Math.atan(Math.tan(v / 2) * cam.aspect);
  cam.position.copy(c).addScaledVector(
    new THREE.Vector3(.7, .6, .9).normalize(),
    (r / 2) / Math.sin(Math.min(v, h) / 2) * 1.08,
  );
  cam.near = r / 500;
  cam.far = r * 20;
  cam.updateProjectionMatrix();
}
function maj() {
  if (!G || !actif) return;
  const L = A.couche, B = Math.min(+$("bas").value, L), C = G.C;
  $("basz").textContent = " couche " + (B + 1) + ", Z " + C[B].z.toFixed(2) +
    " mm";
  G.lignes.geometry.setDrawRange(G.deb[B] * 2, (G.deb[L + 1] - G.deb[B]) * 2);
  G.trajets.visible = $("trajets").checked;
  G.trajets.geometry.setDrawRange(
    G.tdeb[B] * 2,
    (G.tdeb[L + 1] - G.tdeb[B]) * 2,
  );
  G.al.visible = $("voiral").checked;
  G.al.children.forEach((m) => {
    const y = m.position.y;
    m.visible = y <= C[L].z + .01 && y >= C[B].z - 5;
  });
  const rb = G.rub,
    N = Math.max(1, +$("nrub").value || 15),
    m = new THREE.Matrix4(),
    q = new THREE.Quaternion(),
    e = new THREE.Euler(),
    co = new THREE.Color();
  let n = 0;
  if ($("rubans").checked) {
    for (
      let ci = Math.max(B, L - N + 1);
      ci <= L && n < rb.instanceMatrix.count;
      ci++
    ) {
      const h = C[ci].h;
      for (
        let s = G.deb[ci];
        s < G.deb[ci + 1] && n < rb.instanceMatrix.count;
        s++
      ) {
        const p = G.pos,
          o = s * 6,
          dx = p[o + 3] - p[o],
          dz = p[o + 5] - p[o + 2],
          l = Math.hypot(dx, dz);
        if (l < 1e-3) continue;
        e.set(0, -Math.atan2(dz, dx), 0);
        q.setFromEuler(e);
        m.compose(
          new THREE.Vector3(
            (p[o] + p[o + 3]) / 2,
            p[o + 1] - h / 2,
            (p[o + 2] + p[o + 5]) / 2,
          ),
          q,
          new THREE.Vector3(l, h, .42),
        );
        rb.setMatrixAt(n, m);
        co.setRGB(G.col[o], G.col[o + 1], G.col[o + 2]);
        rb.setColorAt(n, co);
        n++;
      }
    }
  }
  rb.count = n;
  rb.instanceMatrix.needsUpdate = true;
  if (rb.instanceColor) rb.instanceColor.needsUpdate = true;
  $("info3d").textContent = "Couches " + (B + 1) + "–" + (L + 1) + " · Z " +
    C[B].z.toFixed(2) + "–" + C[L].z.toFixed(2) + " mm · " +
    (G.deb[L + 1] - G.deb[B]).toLocaleString("fr") + " segments" +
    (n ? " · " + n.toLocaleString("fr") + " en volume" : "");
}
function onglet(t) {
  actif = t === "3d";
  $("vue").style.display = actif ? "none" : "";
  box.style.display = actif ? "block" : "none";
  $("ctl3d").style.display = actif ? "" : "none";
  $("o3d").classList.toggle("actif", actif);
  $("o2d").classList.toggle("actif", !actif);
  if (actif) {
    if (!ren) init();
    taille();
    if (!G) construire();
    maj();
  }
}
$("o2d").onclick = () => onglet("2d");
$("o3d").onclick = () => onglet("3d");
["bas", "rubans", "trajets", "voiral", "nrub"].forEach((i) =>
  $(i).addEventListener("input", maj)
);
$("mode").onchange = construire;
window.maj3d = maj;
window.plaque3d = () => {
  G && scene.remove(G.groupe);
  G = null;
  if (actif) construire();
};
window.apercu3d = {
  onglet,
  get etat() {
    return G &&
      {
        segments: G.deb[G.deb.length - 1],
        trajets: G.tdeb[G.tdeb.length - 1],
        alertes: G.al.children.length,
        rubans: G.rub.count,
      };
  },
};
