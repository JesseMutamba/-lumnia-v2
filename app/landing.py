"""The public landing page — Lumnia's face for anonymous visitors at /.

Server-rendered static HTML like the login pages (no framework, no build
step, self-contained except the product shot served at /landing-shot.jpg).
FR is the default language — the anchor clientele works in French — with a
client-side FR/EN toggle. Every claim on the page is one the product
actually keeps: totals recomputed, unsupported metrics not rendered,
deliverables versioned. The product shot is the real interface rendering
demonstration data and is captioned as such.
"""
from __future__ import annotations

LANDING_HTML = """<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lumnia — Vos tableurs, audités. Vos décisions, éclairées.</title>
<meta name="description" content="Lumnia transforme vos classeurs Excel en tableaux de bord audités et notes d'analyse — chaque total recalculé, chaque contradiction quantifiée.">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Ccircle cx='16' cy='16' r='7' fill='%23c9992a'/%3E%3C/svg%3E">
<style>
  :root{--paper:#f4f0e8;--card:#fbf9f3;--ink:#201d17;--ink2:#5c564a;--ink3:#99917e;
    --gold:#a8821f;--gold2:#c9992a;--border:#e7dfcd;--border2:#d9d0b8}
  *{box-sizing:border-box}
  body{margin:0;background:var(--paper);color:var(--ink);
    font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
  .serif{font-family:"Didot","Bodoni MT","Source Serif 4",Georgia,serif}
  .mono{font-family:ui-monospace,Menlo,Consolas,monospace;letter-spacing:.14em;
    text-transform:uppercase;font-size:11px}
  .wrap{max-width:1080px;margin:0 auto;padding:0 24px}
  html[data-lang="fr"] .en{display:none}
  html[data-lang="en"] .fr{display:none}

  header{display:flex;align-items:center;gap:18px;padding:22px 0}
  .wordmark{font-family:"Didot","Bodoni MT",Georgia,serif;font-size:19px;
    letter-spacing:.16em;text-indent:.16em;font-weight:500;display:flex;
    align-items:center;gap:10px}
  .grow{flex:1}
  .langs{display:flex;border:1px solid var(--border2);border-radius:99px;overflow:hidden}
  .langs button{border:none;background:none;padding:5px 13px;cursor:pointer;
    font:inherit;font-size:11.5px;color:var(--ink2)}
  .langs button.on{background:var(--ink);color:var(--paper)}
  a.ghost{color:var(--ink);text-decoration:none;font-size:13.5px;
    border:1px solid var(--border2);border-radius:99px;padding:7px 16px}
  a.ghost:hover{border-color:var(--gold)}

  .hero{padding:64px 0 34px;max-width:720px}
  .kick{color:var(--gold);margin-bottom:18px}
  h1{font-family:"Didot","Bodoni MT","Source Serif 4",Georgia,serif;
    font-weight:500;font-size:clamp(34px,5.4vw,56px);line-height:1.12;margin:0 0 18px}
  .sub{font-size:17.5px;color:var(--ink2);max-width:56ch;margin:0 0 30px}
  .ctas{display:flex;gap:12px;flex-wrap:wrap}
  a.cta{background:var(--gold);color:#fff;text-decoration:none;border-radius:8px;
    padding:13px 26px;font-family:ui-monospace,Menlo,monospace;font-size:11.5px;
    letter-spacing:.12em;text-transform:uppercase}
  a.cta:hover{background:var(--gold2)}
  a.cta2{border:1px solid var(--border2);color:var(--ink);text-decoration:none;
    border-radius:8px;padding:13px 26px;font-family:ui-monospace,Menlo,monospace;
    font-size:11.5px;letter-spacing:.12em;text-transform:uppercase}
  a.cta2:hover{border-color:var(--gold)}

  .shotframe{margin:44px 0 0;border:1px solid var(--border2);background:var(--card);
    border-radius:10px;overflow:hidden}
  .shotbar{display:flex;gap:6px;padding:10px 14px;border-bottom:1px solid var(--border)}
  .shotbar span{width:9px;height:9px;border-radius:50%;background:var(--border2)}
  .shotframe img{display:block;width:100%;height:auto}
  .shotcap{text-align:center;color:var(--ink3);font-size:12px;margin:10px 0 0}

  section{padding:72px 0 0}
  .sechead{color:var(--gold);margin-bottom:10px}
  h2{font-family:"Didot","Bodoni MT","Source Serif 4",Georgia,serif;font-weight:500;
    font-size:clamp(24px,3.4vw,34px);line-height:1.2;margin:0 0 8px;max-width:24ch}
  .props{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));
    gap:0;border-top:1px solid var(--border2);margin-top:26px}
  .prop{padding:26px 26px 30px;border-bottom:1px solid var(--border)}
  .prop+.prop{border-left:1px solid var(--border)}
  @media(max-width:860px){.prop+.prop{border-left:none}}
  .prop b{font-family:"Didot",Georgia,serif;font-size:19px;font-weight:600;display:block;margin:6px 0}
  .prop p{color:var(--ink2);margin:0;font-size:14px}

  .steps{counter-reset:s;border-top:1px solid var(--border2);margin-top:26px}
  .step{display:flex;gap:22px;align-items:baseline;padding:22px 4px;
    border-bottom:1px solid var(--border)}
  .step .n{font-family:ui-monospace,Menlo,monospace;color:var(--gold);font-size:13px}
  .step b{font-family:"Didot",Georgia,serif;font-size:18px;font-weight:600}
  .step p{color:var(--ink2);margin:3px 0 0;font-size:14px;max-width:64ch}

  .band{margin-top:84px;background:var(--ink);color:var(--paper);
    padding:56px 0 60px;text-align:center}
  .band h2{color:var(--paper);margin:14px auto 8px;max-width:none}
  .band .sub{color:#b8b0a0;margin:0 auto 26px;max-width:52ch}
  footer{padding:30px 0 40px;display:flex;gap:16px;align-items:center;
    color:var(--ink3);font-size:12.5px}
  footer a{color:var(--ink3)}
</style></head><body>
<div class="wrap">
  <header>
    <span class="wordmark"><svg width="30" height="30" viewBox="-50 -50 100 100" class="sun"></svg>LUMNIA</span>
    <span class="grow"></span>
    <span class="langs"><button data-l="fr">FR</button><button data-l="en">EN</button></span>
    <a class="ghost" href="/portal/signin"><span class="fr">Espace client</span><span class="en">Client sign-in</span></a>
  </header>

  <div class="hero">
    <div class="kick mono"><span class="fr">Intelligence financière &amp; opérationnelle</span><span class="en">Financial &amp; operational intelligence</span></div>
    <h1><span class="fr">Vos tableurs ont des réponses.<br>Nous les auditons.</span><span class="en">Your spreadsheets hold answers.<br>We audit them.</span></h1>
    <p class="sub"><span class="fr">Envoyez vos classeurs tels qu'ils sont — multi-feuilles, en français, imparfaits.
      Lumnia recalcule chaque total, quantifie chaque contradiction, puis vous rend des tableaux
      de bord et des notes d'analyse prêts pour la décision.</span>
      <span class="en">Send your workbooks exactly as they are — multi-sheet, messy, in any shape.
      Lumnia re-computes every total, quantifies every contradiction, and returns decision-ready
      dashboards and written analysis briefs.</span></p>
    <div class="ctas">
      <a class="cta" href="/portal/signin"><span class="fr">Demander un accès</span><span class="en">Request access</span></a>
      <a class="cta2" href="/portal/signin"><span class="fr">J'ai déjà un compte</span><span class="en">I have an account</span></a>
    </div>
  </div>

  <div class="shotframe">
    <div class="shotbar"><span></span><span></span><span></span></div>
    <img src="/landing-shot.jpg" width="1240" height="1033"
      alt="Tableau de bord Lumnia — coût par tonne réel contre budget, part investie, chemin vers la rentabilité">
  </div>
  <p class="shotcap"><span class="fr">L'interface réelle — données de démonstration.</span>
    <span class="en">The real interface — demonstration data.</span></p>

  <section>
    <div class="sechead mono"><span class="fr">Pourquoi Lumnia</span><span class="en">Why Lumnia</span></div>
    <h2><span class="fr">La confiance est le produit.</span><span class="en">Trust is the product.</span></h2>
    <div class="props">
      <div class="prop"><span class="mono" style="color:var(--gold)">01</span>
        <b><span class="fr">Audité d'abord</span><span class="en">Audited first</span></b>
        <p><span class="fr">Chaque feuille est lue, chaque total recalculé, chaque contradiction
          chiffrée — avant le moindre graphique.</span>
          <span class="en">Every sheet read, every total re-computed, every contradiction
          quantified — before a single chart is drawn.</span></p></div>
      <div class="prop"><span class="mono" style="color:var(--gold)">02</span>
        <b><span class="fr">Honnête par construction</span><span class="en">Honest by construction</span></b>
        <p><span class="fr">Un chiffre que vos données ne soutiennent pas ne s'affiche pas.
          À la place : ce qui manque pour l'obtenir.</span>
          <span class="en">A figure your data can't support doesn't render. Instead:
          exactly what's missing to get it.</span></p></div>
      <div class="prop"><span class="mono" style="color:var(--gold)">03</span>
        <b><span class="fr">Versionné, traçable</span><span class="en">Versioned, traceable</span></b>
        <p><span class="fr">Chaque livrable arrive dans votre espace en lecture seule,
          versionné. Rien n'est remplacé en silence.</span>
          <span class="en">Every deliverable lands in your read-only hub, versioned.
          Nothing is silently replaced.</span></p></div>
    </div>
  </section>

  <section>
    <div class="sechead mono"><span class="fr">Comment ça marche</span><span class="en">How it works</span></div>
    <h2><span class="fr">Du classeur brut à la décision.</span><span class="en">From raw workbook to decision.</span></h2>
    <div class="steps">
      <div class="step"><span class="n">01</span><div>
        <b><span class="fr">Envoyez votre classeur</span><span class="en">Send your workbook</span></b>
        <p><span class="fr">Depuis votre espace client, tel quel — XLSX, XLS, ODS ou CSV.</span>
          <span class="en">From your client hub, exactly as it is — XLSX, XLS, ODS or CSV.</span></p></div></div>
      <div class="step"><span class="n">02</span><div>
        <b><span class="fr">Nous l'auditons</span><span class="en">We audit it</span></b>
        <p><span class="fr">Votre analyste passe chaque chiffre au crible : totaux rapprochés,
          écarts contre budget, coût par tonne, trésorerie.</span>
          <span class="en">Your analyst puts every figure through the mill: reconciled totals,
          variance against budget, cost per tonne, cash.</span></p></div></div>
      <div class="step"><span class="n">03</span><div>
        <b><span class="fr">Recevez vos livrables</span><span class="en">Receive your deliverables</span></b>
        <p><span class="fr">Un tableau de bord exécutif et une note d'analyse rédigée — chaque
          chiffre tracé jusqu'à vos propres données.</span>
          <span class="en">An executive dashboard and a written analysis brief — every figure
          traced back to your own records.</span></p></div></div>
    </div>
  </section>
</div>

<div class="band">
  <div class="wrap">
    <span class="mono" style="color:var(--gold2)"><span class="fr">Commencer</span><span class="en">Get started</span></span>
    <h2><span class="fr">Votre email professionnel suffit.</span><span class="en">Your work email is enough.</span></h2>
    <p class="sub"><span class="fr">Demandez un accès — votre analyste vous ouvre votre espace,
      et votre premier classeur peut partir aujourd'hui.</span>
      <span class="en">Request access — your analyst opens your hub, and your first
      workbook can go out today.</span></p>
    <a class="cta" href="/portal/signin"><span class="fr">Demander un accès</span><span class="en">Request access</span></a>
  </div>
</div>

<div class="wrap">
  <footer>
    <span class="serif" style="font-style:italic;color:var(--gold)">Light where there was none.</span>
    <span class="grow"></span>
    <a href="/login"><span class="fr">Analyste ? Connexion</span><span class="en">Analyst? Sign in</span></a>
  </footer>
</div>

<script>
  const setLang = l => { document.documentElement.dataset.lang = l;
    document.querySelectorAll(".langs button").forEach(b =>
      b.classList.toggle("on", b.dataset.l === l));
    try { localStorage.setItem("lumnia_lang", l); } catch (_) {} };
  document.querySelectorAll(".langs button").forEach(b =>
    b.addEventListener("click", () => setLang(b.dataset.l)));
  let saved = "fr";
  try { saved = localStorage.getItem("lumnia_lang") || "fr"; } catch (_) {}
  setLang(saved === "en" ? "en" : "fr");
  let d = `<circle cx="0" cy="0" r="7.5" fill="#c9992a"/>`;
  for (let r = 1; r <= 7; r++) { const R = 9 + r * 4.6, n = 10 + r * 6,
    rr = Math.max(.5, 2.1 - r * .18), o = Math.max(.3, 1 - r * .1);
    for (let k = 0; k < n; k++) { const a = k / n * 6.283 + r * .35;
      d += `<circle cx="${(R * Math.cos(a)).toFixed(1)}" cy="${(R * Math.sin(a)).toFixed(1)}" r="${rr}" fill="#c9992a" opacity="${o.toFixed(2)}"/>`; } }
  document.querySelectorAll("svg.sun").forEach(s => s.innerHTML = d);
</script>
</body></html>"""
