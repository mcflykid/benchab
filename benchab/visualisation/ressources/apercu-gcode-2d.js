// Trace le G-code conservé depuis les seules données intégrées, sans appeler de serveur.
const echapper = (s) =>
  String(s).replace(
    /[&<>"']/g,
    (c) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[c]),
  );
const D = __DONNEES__;
const cv = document.getElementById("c"), cx = cv.getContext("2d");
let P = 0, L = 0, vue = null, cache = {};
const masque = new Set();
function lignes(k) {
  const id = P + ":" + k;
  if (cache[id]) return cache[id];
  const b = atob(D.plaques[P].couches[k].d), u = new Uint8Array(b.length);
  for (let i = 0; i < b.length; i++) u[i] = b.charCodeAt(i);
  return cache[id] = new Int16Array(u.buffer);
}
function bornes() {
  let a = [1e9, 1e9, -1e9, -1e9];
  const C = D.plaques[P].couches;
  for (let k = 0; k < C.length; k += Math.max(1, C.length >> 4)) {
    const d = lignes(k);
    let i = 0;
    while (i < d.length) {
      const n = d[i + 1];
      for (let j = 0; j <= n; j++) {
        const x = d[i + 2 + 2 * j], y = d[i + 3 + 2 * j];
        if (x < a[0]) a[0] = x;
        if (y < a[1]) a[1] = y;
        if (x > a[2]) a[2] = x;
        if (y > a[3]) a[3] = y;
      }
      i += 4 + 2 * n;
    }
  }
  return a.map((v) => v / D.q);
}
function recadrer() {
  const b = bornes(),
    w = cv.clientWidth,
    h = cv.clientHeight,
    s = 0.9 * Math.min(w / (b[2] - b[0] || 1), h / (b[3] - b[1] || 1));
  vue = {
    s,
    ox: w / 2 - s * (b[0] + b[2]) / 2,
    oy: h / 2 + s * (b[1] + b[3]) / 2,
  };
  dessiner();
}
const ecran = (x, y) => [vue.ox + vue.s * x, vue.oy - vue.s * y],
  monde = (u, v) => [(u - vue.ox) / vue.s, (vue.oy - v) / vue.s];
function trace(k, gris) {
  const d = lignes(k);
  let i = 0;
  cx.lineWidth = Math.max(1, 0.42 * vue.s);
  cx.lineCap = "round";
  cx.lineJoin = "round";
  while (i < d.length) {
    const t = d[i], n = d[i + 1];
    if (!masque.has(t)) {
      cx.strokeStyle = gris ? "#555a" : D.couleurs[t];
      cx.beginPath();
      for (let j = 0; j <= n; j++) {
        const p = ecran(d[i + 2 + 2 * j] / D.q, d[i + 3 + 2 * j] / D.q);
        j ? cx.lineTo(p[0], p[1]) : cx.moveTo(p[0], p[1]);
      }
      cx.stroke();
    }
    i += 4 + 2 * n;
  }
}
function dessiner() {
  const r = devicePixelRatio || 1;
  cv.width = cv.clientWidth * r;
  cv.height = cv.clientHeight * r;
  cx.setTransform(r, 0, 0, r, 0, 0);
  cx.fillStyle = "#14161c";
  cx.fillRect(0, 0, cv.width, cv.height);
  if (!vue) return;
  const C = D.plaques[P].couches, c = C[L];
  if (document.getElementById("prec").checked && L > 0) trace(L - 1, true);
  trace(L, false);
  if (L === 0) {
    for (const f of D.plaques[P].defauts) {
      const p = ecran(f.xy[0], f.xy[1]);
      cx.strokeStyle = "#ff3b3b";
      cx.lineWidth = 2;
      cx.beginPath();
      cx.arc(p[0], p[1], Math.max(10, 3 * vue.s), 0, 7);
      cx.stroke();
    }
  }
  let cum = 0;
  for (let k = 0; k <= L; k++) cum += C[k].t;
  document.getElementById("num").textContent = (L + 1) + " / " + C.length;
  document.getElementById("z").textContent = c.z.toFixed(2);
  document.getElementById("info").textContent = "Z " + c.z.toFixed(2) +
    " mm · couche " + c.h.toFixed(2) + " mm · ≈ " + fmt(c.t) + " (cumul ≈ " +
    fmt(cum) + ")";
}
const fmt = (s) =>
  s < 60
    ? Math.round(s) + " s"
    : s < 3600
    ? Math.floor(s / 60) + " min " + String(Math.round(s % 60)).padStart(2, "0")
    : Math.floor(s / 3600) + " h " +
      String(Math.floor(s % 3600 / 60)).padStart(2, "0");
function aller(k) {
  const C = D.plaques[P].couches;
  L = Math.max(0, Math.min(C.length - 1, k));
  document.getElementById("couche").value = L;
  dessiner();
  window.maj3d && window.maj3d();
}
function plaque(k) {
  P = k;
  cache = {};
  const C = D.plaques[P].couches;
  const s = document.getElementById("couche");
  s.max = C.length - 1;
  L = 0;
  s.value = 0;
  const ds = D.plaques[P].defauts;
  document.getElementById("defs").innerHTML = ds.length
    ? '<p class="doux">Défauts de 1re couche (contrôle premiere_couche)</p>' +
      ds.map((f) =>
        '<div class="def">● ' +
        echapper(
          f.type + " — " + f.aire_mm2 + " mm² (X " + f.xy[0] + ", Y " +
            f.xy[1] + ")",
        ) + "</div>"
      ).join("")
    : "";
  document.querySelectorAll(".def").forEach((e, i) =>
    e.onclick = () => {
      aller(0);
      const f = ds[i];
      vue.s = Math.max(vue.s, 12);
      vue.ox = cv.clientWidth / 2 - vue.s * f.xy[0];
      vue.oy = cv.clientHeight / 2 + vue.s * f.xy[1];
      dessiner();
    }
  );
  recadrer();
  window.plaque3d && window.plaque3d();
}
const sel = document.getElementById("plaque");
D.plaques.forEach((p, i) => sel.add(new Option("Plaque " + p.n, i)));
sel.onchange = () => plaque(+sel.value);
const ty = document.getElementById("types");
D.types.forEach((t, i) => {
  const l = document.createElement("label");
  l.innerHTML =
    '<input type="checkbox" checked><span class="pastille" style="background:' +
    D.couleurs[i] + '"></span>' + t;
  l.querySelector("input").onchange = (e) => {
    e.target.checked ? masque.delete(i) : masque.add(i);
    dessiner();
  };
  ty.appendChild(l);
});
document.getElementById("couche").oninput = (e) => aller(+e.target.value);
document.getElementById("moins").onclick = () => aller(L - 1);
document.getElementById("plus").onclick = () => aller(L + 1);
document.getElementById("prec").onchange = dessiner;
document.getElementById("recadrer").onclick = recadrer;
addEventListener("keydown", (e) => {
  if (e.key === "ArrowRight" || e.key === "ArrowUp") {
    aller(L + 1);
    e.preventDefault();
  }
  if (e.key === "ArrowLeft" || e.key === "ArrowDown") {
    aller(L - 1);
    e.preventDefault();
  }
});
cv.addEventListener("wheel", (e) => {
  e.preventDefault();
  const f = Math.exp(-e.deltaY * 0.0015),
    r = cv.getBoundingClientRect(),
    u = e.clientX - r.left,
    v = e.clientY - r.top;
  vue.ox = u - (u - vue.ox) * f;
  vue.oy = v - (v - vue.oy) * f;
  vue.s *= f;
  dessiner();
}, { passive: false });
let glisse = null;
cv.onmousedown = (e) =>
  glisse = { x: e.clientX, y: e.clientY, ox: vue.ox, oy: vue.oy, bouge: false };
addEventListener("mousemove", (e) => {
  if (!glisse) return;
  const dx = e.clientX - glisse.x, dy = e.clientY - glisse.y;
  if (Math.abs(dx) + Math.abs(dy) > 3) glisse.bouge = true;
  vue.ox = glisse.ox + dx;
  vue.oy = glisse.oy + dy;
  dessiner();
});
addEventListener("mouseup", (e) => {
  if (glisse && !glisse.bouge) {
    const r = cv.getBoundingClientRect(),
      m = monde(e.clientX - r.left, e.clientY - r.top),
      z = D.plaques[P].couches[L].z,
      t = "Plaque " + D.plaques[P].n + ", couche " + (L + 1) + " : X " +
        m[0].toFixed(1) + " Y " + m[1].toFixed(1) + " Z " + z.toFixed(2);
    const b = document.getElementById("clic");
    b.style.display = "block";
    b.innerHTML = t + '<br><button id="cp">Copier</button>';
    document.getElementById("cp").onclick = () =>
      navigator.clipboard && navigator.clipboard.writeText(t);
  }
  glisse = null;
});
addEventListener("resize", dessiner);
window.apercu = {
  D,
  aller,
  plaque,
  get couche() {
    return L;
  },
  get plaqueIndex() {
    return P;
  },
};
plaque(0);
