"""Sistema visual (plan de diseño §7–§8): tokens de color por plataforma y modo oscuro."""

from __future__ import annotations

import platform

PLATFORM = "win" if platform.system() == "Windows" else "mac"
MOD = "Ctrl" if PLATFORM == "win" else "⌘"

CSS = """
:root {
  --fondo:#FFFFFF; --fondo-lateral:#F2F1EF; --fondo-suave:#FBFAF9; --borde:#E6E4E1; --borde-suave:#F0EEEB;
  --texto:#1D1D1F; --texto-2:#55555A; --texto-3:#6E6E73;
  --acento:#0A6CDB; --acento-suave:#E6EFFB; --grabar:#D92D3A; --ok:#1F7A46; --ok-punto:#1F9D55;
  --profesor:#4B2FB0; --profesor-bg:#EFEBFB; --yo:#0A5BBE; --yo-bg:#E6EFFB;
  --part:#4A4A4F; --part-bg:#EEEDEB; --revisar:#FFF3A8; --aviso-bg:#FFF6E5; --aviso:#8A5300;
  --info-bg:#F4F8FE; --info-borde:#D5E3F7; --radio-control:8px; --radio-tarjeta:14px;
  --fuente:-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif;
}
body.platform-win {
  --fondo:#FAFAFA; --fondo-lateral:#F3F3F3; --borde:#E5E5E5; --texto:#1B1B1B; --texto-2:#5F5F5F;
  --acento:#005FB8; --grabar:#C42B1C; --radio-control:4px; --radio-tarjeta:8px;
}
body.body--dark {
  --fondo:#1E1E1F; --fondo-lateral:#2A2A2C; --fondo-suave:#242426; --borde:#3A3A3C; --borde-suave:#323234;
  --texto:#F2F2F4; --texto-2:#C4C4C8; --texto-3:#A1A1A6; --acento:#4C9AFF; --acento-suave:#1F3350;
  --profesor:#C9B8FF; --profesor-bg:#33285A; --yo:#9CC4FF; --yo-bg:#1F3350; --part:#D0D0D4; --part-bg:#3A3A3C;
  --revisar:#5C4F00; --aviso-bg:#3D2E12; --aviso:#FFD28A; --info-bg:#1C2A3D; --info-borde:#2E4A70; --ok:#5BD08E;
}
html, body { background: var(--fondo); color: var(--texto); font-family: var(--fuente); font-size: 13px; }
.q-page, .nicegui-content { padding: 0 !important; }
.app-shell { display:flex; height:100vh; width:100vw; overflow:hidden; background:var(--fondo); }
.sidebar { width:232px; flex-shrink:0; background:var(--fondo-lateral); border-right:1px solid var(--borde);
           display:flex; flex-direction:column; padding:14px 10px; gap:2px; }
.sidebar .brand { font-weight:700; font-size:15px; padding:6px 10px 14px; display:flex; align-items:center; gap:8px; }
.nav-item { display:flex; align-items:center; gap:10px; padding:7px 10px; border-radius:var(--radio-control);
            color:var(--texto); text-decoration:none; cursor:pointer; font-size:13px; }
.nav-item:hover { background:rgba(127,127,127,.12); }
.nav-item.active { background:var(--acento); color:#fff; }
.nav-item .q-icon { font-size:17px; }
.main { flex:1; min-width:0; display:flex; flex-direction:column; }
.topbar { height:52px; flex-shrink:0; display:flex; align-items:center; gap:16px; padding:0 20px 0 24px;
          border-bottom:1px solid var(--borde); }
.topbar h1 { margin:0; font-size:15px; font-weight:700; line-height:1.2; }
.topbar .sub { font-size:11px; color:var(--texto-3); }
.content { flex:1; min-height:0; overflow:auto; padding:24px 28px; display:flex; flex-direction:column; gap:20px; }
.card { border:1px solid var(--borde); border-radius:var(--radio-tarjeta); padding:18px 20px; background:var(--fondo); }
.card.soft { background:var(--fondo-suave); }
.content .card, .content > .banner { width:100%; box-sizing:border-box; }
.card h2 { margin:0; font-size:15px; font-weight:700; }
.muted { color:var(--texto-3); }
.muted2 { color:var(--texto-2); }
.small { font-size:11.5px; }
.tnum { font-variant-numeric: tabular-nums; }
.q-btn.btn-rec { background:var(--grabar) !important; color:#fff !important; border-radius:var(--radio-control) !important;
           font-weight:600; text-transform:none; }
.q-btn.btn-primary { background:var(--acento) !important; color:#fff !important; border-radius:var(--radio-control) !important;
               text-transform:none; font-weight:600; }
.q-btn.btn-plain { background:transparent !important; color:var(--texto) !important; border:1px solid var(--borde);
             border-radius:var(--radio-control) !important; text-transform:none; box-shadow:none !important; }
.q-btn { text-transform:none; }
.q-btn.btn-plain .q-focus-helper, .q-btn.btn-rec .q-focus-helper { display:none; }
.rec-dot { width:8px; height:8px; border-radius:50%; background:#fff; display:inline-block; }
.speaker { display:inline-flex; align-items:center; height:20px; padding:0 8px; border-radius:10px; font-size:11px;
           font-weight:600; white-space:nowrap; }
.speaker.profesor { color:var(--profesor); background:var(--profesor-bg); }
.speaker.yo { color:var(--yo); background:var(--yo-bg); }
.speaker.participante { color:var(--part); background:var(--part-bg); }
.transcript-line { display:flex; gap:12px; padding:8px 0; font-size:15px; line-height:1.55; }
.transcript-line .ts { color:var(--texto-3); font-size:12px; padding-top:3px; min-width:62px; }
mark, .doubt { background:var(--revisar); color:inherit; border-radius:3px; padding:0 2px; }
.banner { display:flex; align-items:center; gap:12px; padding:10px 14px; border-radius:12px; }
.banner.info { background:var(--info-bg); border:1px solid var(--info-borde); }
.banner.warn { background:var(--aviso-bg); color:var(--aviso); }
.banner.purple { background:var(--profesor-bg); color:var(--profesor); }
.banner.ok { background:#E8F5EE; color:var(--ok); }
body.body--dark .banner.ok { background:#16301F; }
.pill { display:inline-flex; align-items:center; height:22px; padding:0 8px; border-radius:11px; font-size:11px;
        font-weight:600; background:var(--part-bg); color:var(--part); }
.pill.warn { background:#FFF6C2; color:#6B5300; }
.pill.ok { background:#E8F5EE; color:var(--ok); }
.list-row { display:flex; align-items:center; gap:12px; padding:10px 0; border-bottom:1px solid var(--borde-suave); }
.list-row:last-child { border-bottom:0; }
.clickable { cursor:pointer; border-radius:8px; }
.clickable:hover { background:rgba(127,127,127,.08); }
.course-bar { width:4px; align-self:stretch; border-radius:2px; }
.meter { height:6px; border-radius:3px; background:var(--borde); overflow:hidden; width:120px; }
.meter > div { height:100%; background:var(--ok-punto); transition:width .15s; }
.source-option { border:1px solid var(--borde); border-radius:12px; padding:12px 14px; cursor:pointer; display:flex; gap:12px; }
.source-option.selected { border-color:var(--acento); box-shadow:0 0 0 1px var(--acento) inset; background:var(--info-bg); }
.settings-nav .nav-item { padding:8px 12px; }
.chat-bubble { max-width:760px; padding:12px 16px; border-radius:14px; line-height:1.55; font-size:14px; }
.chat-bubble.user { background:var(--acento); color:#fff; align-self:flex-end; }
.chat-bubble.bot { background:var(--fondo-lateral); align-self:flex-start; }
.chat-bubble.bot p { margin:0 0 6px; }
.status-card { margin-top:auto; padding:10px; border-radius:10px; background:rgba(127,127,127,.08); font-size:11.5px; }
.kbd { font-size:11px; color:var(--texto-3); border:1px solid var(--borde); border-radius:4px; padding:0 4px; }
:focus-visible { outline:2px solid var(--acento); outline-offset:2px; }
"""


def _layered(css: str) -> str:
    """Reglas con !important dentro de la capa «theme»: así ganan a las de Quasar (capas CSS)."""
    import re

    rules = re.findall(r"[^{}]+\{[^{}]*\}", css)
    important = "".join(r for r in rules if "!important" in r)
    normal = "".join(r for r in rules if "!important" not in r)
    return f"{normal}@layer theme {{{important}}}"


def head_html() -> str:
    return f"<style>{_layered(CSS)}</style><script>document.addEventListener('DOMContentLoaded',()=>document.body.classList.add('platform-{PLATFORM}'));</script>"


COURSE_COLORS = [
    "#0A6CDB",
    "#C2410C",
    "#15803D",
    "#7C3AED",
    "#B91C1C",
    "#0E7490",
    "#A16207",
    "#BE185D",
]


def course_color(name: str) -> str:
    return COURSE_COLORS[sum(map(ord, name)) % len(COURSE_COLORS)]
