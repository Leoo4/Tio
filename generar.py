#!/usr/bin/env python3
"""El Tío Ahorrador: convierte links de afiliado de Mercado Libre en publicaciones.

Uso:  python generar.py
Lee links.txt (un link por línea) y crea, por cada producto, una carpeta en
salida/ con: imagen.jpg (1080x1080), facebook.txt e instagram.txt.
"""
import html
import json
import random
import re
import sys
import time
import unicodedata
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from PIL import Image, ImageDraw, ImageFont

BASE = Path(__file__).resolve().parent
SALIDA = BASE / "salida"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "es-MX,es;q=0.9",
}

NEGRO, ROJO, GRIS = (17, 17, 17), (232, 67, 46), (110, 110, 110)
BLANCO, TARJETA = (255, 255, 255), (244, 244, 242)

INTROS = [
    "👀 El tío encontró esta oferta y no se la quiso guardar:",
    "😎 Hazle caso al tío: esto está barato.",
    "🔥 Ofertón cazado por el tío:",
    "💸 El tío dice: ¡aprovecha antes de que suba!",
    "🧐 El tío ya revisó el precio y sí vale la pena:",
]


# ---------------------------------------------------------------- datos
def numero(texto):
    """'1,299.50' o '1299' -> 1299.5"""
    if texto is None:
        return None
    try:
        return float(str(texto).replace(",", "").strip())
    except ValueError:
        return None


def meta(pagina, prop):
    for patron in (
        r'<meta[^>]+(?:property|name|itemprop)=["\']%s["\'][^>]*content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name|itemprop)=["\']%s["\']',
    ):
        m = re.search(patron % re.escape(prop), pagina)
        if m:
            return html.unescape(m.group(1))
    return None


def producto_jsonld(pagina):
    for bloque in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', pagina, re.S):
        try:
            data = json.loads(bloque)
        except ValueError:
            continue
        pila = data if isinstance(data, list) else [data]
        while pila:
            d = pila.pop()
            if isinstance(d, dict):
                if d.get("@type") == "Product":
                    return d
                pila.extend(d.get("@graph", []))
    return None


def tarjeta_destacada(pagina):
    """Los links meli.la llevan al perfil social del afiliado
    (mercadolibre.com.mx/social/...), donde el producto compartido aparece en el
    bloque "card-featured" del JSON que viene incrustado en la página."""
    i = pagina.find('"id":"card-featured"')
    j = pagina.find('"polycards":', i)
    if i < 0 or j < 0:
        return None
    try:
        tarjetas, _ = json.JSONDecoder().raw_decode(pagina, j + len('"polycards":'))
        t = tarjetas[0]
        partes = {c["type"]: c.get(c["type"]) or {} for c in t["components"]}
        precio = partes.get("price", {})
        foto = t["pictures"]["pictures"][0]["id"]
    except (ValueError, LookupError, TypeError):
        return None
    return {
        "titulo": partes.get("title", {}).get("text"),
        "precio": numero((precio.get("current_price") or {}).get("value")),
        "antes": numero((precio.get("previous_price") or {}).get("value")),
        "imagen": f"https://http2.mlstatic.com/D_NQ_NP_{foto}-O.webp",
    }


def leer_producto(pagina):
    destacada = tarjeta_destacada(pagina)
    if destacada and destacada["titulo"] and destacada["precio"]:
        return destacada
    # Página normal de producto
    p = producto_jsonld(pagina) or {}
    oferta = p.get("offers") or {}
    if isinstance(oferta, list):
        oferta = oferta[0] if oferta else {}
    imagen = p.get("image")
    if isinstance(imagen, list):
        imagen = imagen[0] if imagen else None
    antes = re.search(r'Antes:\s*([\d.,]+)\s*pesos', pagina)
    return {
        "titulo": p.get("name") or meta(pagina, "og:title"),
        "precio": numero(oferta.get("price")) or numero(meta(pagina, "price")),
        "antes": numero(antes.group(1)) if antes else None,
        "imagen": imagen or meta(pagina, "og:image"),
    }


def obtener(link):
    r = requests.get(link, headers=HEADERS, timeout=25)
    r.raise_for_status()
    datos = leer_producto(r.content.decode("utf-8", "replace"))
    # matt_word = nombre del afiliado que cobra la comisión de este link
    datos["afiliado"] = parse_qs(urlparse(r.url).query).get("matt_word", [None])[0]
    return datos


def leer_links():
    """Cada línea: link   o bien   link | título | precio | precio antes | url imagen"""
    filas = []
    for linea in (BASE / "links.txt").read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#"):
            continue
        partes = [x.strip() for x in linea.split("|")] + [""] * 5
        filas.append({"link": partes[0], "titulo": partes[1] or None,
                      "precio": numero(partes[2] or None), "antes": numero(partes[3] or None),
                      "imagen": partes[4] or None})
    return filas


# ---------------------------------------------------------------- imagen
def fuente(tam, negrita=True):
    nombres = (["arialbd.ttf", "Arial Bold.ttf", "LiberationSans-Bold.ttf", "segoeuib.ttf",
                "DejaVuSans-Bold.ttf"] if negrita
               else ["arial.ttf", "Arial.ttf", "LiberationSans-Regular.ttf", "segoeui.ttf",
                     "DejaVuSans.ttf"])
    carpetas = [BASE, Path("C:/Windows/Fonts"), Path("/Library/Fonts"),
                Path("/System/Library/Fonts/Supplemental"),
                Path("/usr/share/fonts/truetype/liberation"), Path("/usr/share/fonts/truetype/dejavu")]
    for n in nombres:
        for c in carpetas:
            if (c / n).exists():
                return ImageFont.truetype(str(c / n), tam)
    return ImageFont.load_default(tam)


def pesos(x):
    return f"${x:,.0f}" if x == int(x) else f"${x:,.2f}"


def renglones(draw, texto, f, ancho, maximo=2):
    palabras, lineas, actual = texto.split(), [], ""
    for p in palabras:
        prueba = f"{actual} {p}".strip()
        if draw.textlength(prueba, font=f) <= ancho:
            actual = prueba
        else:
            lineas.append(actual)
            actual = p
    lineas.append(actual)
    if len(lineas) > maximo:
        lineas = lineas[:maximo]
        while draw.textlength(lineas[-1] + "…", font=f) > ancho:
            lineas[-1] = lineas[-1][:-1]
        lineas[-1] = lineas[-1].rstrip() + "…"
    return lineas


def crear_imagen(d, foto, destino):
    img = Image.new("RGB", (1080, 1080), BLANCO)
    dr = ImageDraw.Draw(img)

    # Marca
    dr.polygon([(60, 70), (78, 48), (122, 48), (122, 92), (78, 92)], fill=ROJO)
    dr.ellipse((74, 64, 82, 76), fill=BLANCO)
    dr.text((140, 70), "Tío Ahorrador", font=fuente(52), fill=NEGRO, anchor="lm")
    dr.text((1020, 70), "OFERTA", font=fuente(34), fill=ROJO, anchor="rm")

    # Foto del producto
    dr.rounded_rectangle((60, 130, 1020, 720), radius=36, fill=TARJETA)
    if foto is not None:
        foto = foto.convert("RGBA")
        foto.thumbnail((820, 520))
        fondo = Image.new("RGBA", foto.size, BLANCO + (255,))
        fondo.alpha_composite(foto)
        caja = Image.new("RGB", (foto.width + 40, foto.height + 40), BLANCO)
        caja.paste(fondo.convert("RGB"), (20, 20))
        img.paste(caja, (540 - caja.width // 2, 425 - caja.height // 2))

    # Descuento
    if d["antes"] and d["precio"] and d["antes"] > d["precio"]:
        pct = round(100 * (1 - d["precio"] / d["antes"]))
        dr.ellipse((830, 155, 1000, 325), fill=ROJO)
        dr.text((915, 240), f"-{pct}%", font=fuente(52), fill=BLANCO, anchor="mm")

    # Título
    f_tit = fuente(40)
    for i, linea in enumerate(renglones(dr, d["titulo"] or "", f_tit, 960)):
        dr.text((60, 750 + i * 50), linea, font=f_tit, fill=NEGRO)

    # Precios
    f_precio = fuente(104)
    actual = pesos(d["precio"]) if d["precio"] else "[PRECIO]"
    dr.text((60, 955), actual, font=f_precio, fill=NEGRO, anchor="ls")
    if d["antes"] and d["precio"] and d["antes"] > d["precio"]:
        x = 60 + dr.textlength(actual, font=f_precio) + 32
        f_antes = fuente(50, negrita=False)
        texto = pesos(d["antes"])
        dr.text((x, 945), texto, font=f_antes, fill=GRIS, anchor="ls")
        dr.line((x, 928, x + dr.textlength(texto, font=f_antes), 928), fill=GRIS, width=4)

    dr.text((60, 1030), "Precio sujeto a cambios · Contiene enlace de afiliado",
            font=fuente(26, negrita=False), fill=GRIS, anchor="ls")
    img.save(destino, quality=92)


# ---------------------------------------------------------------- textos
def linea_precio(d):
    if not d["precio"]:
        return "💰 Precio: [REVISA EL PRECIO]"
    texto = f"💰 Ahora: {pesos(d['precio'])}"
    if d["antes"] and d["antes"] > d["precio"]:
        pct = round(100 * (1 - d["precio"] / d["antes"]))
        texto += f" (antes {pesos(d['antes'])}, -{pct}%)"
    return texto


def captions(d, n):
    intro = random.choice(INTROS)
    cuerpo = f"{intro}\n\n{d['titulo']}\n{linea_precio(d)}\n\n"
    fin = "\n\n⚠️ El precio puede cambiar en cualquier momento.\n"
    fb = (cuerpo + f"👉 Cómpralo aquí: {d['link']}" + fin +
          "#ElTíoAhorrador #Ofertas #MercadoLibre")
    ig = (cuerpo + f"🔗 Link de compra en mi bio (oferta #{n})" + fin +
          "#ElTíoAhorrador #Ofertas #MercadoLibre #Descuentos #OfertasMX "
          "#Ahorro #Tecnologia #Hogar")
    return fb, ig


def carpeta(n, titulo):
    sin_acentos = unicodedata.normalize("NFKD", titulo or "producto").encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", sin_acentos.lower())[:40].strip("-")
    return SALIDA / f"{n:02d}-{slug}"


# ---------------------------------------------------------------- página "link en bio"
PAGINA = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>El Tío Ahorrador · Ofertas</title>
<meta name="description" content="Las ofertas de Mercado Libre que encontró el Tío Ahorrador.">
<style>
  :root {{ --negro:#111; --rojo:#e8432e; --gris:#6e6e6e; --fondo:#f4f4f2; --tarjeta:#fff; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--fondo); color:var(--negro);
         font-family:system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif; }}
  main {{ max-width:560px; margin:0 auto; padding:24px 16px 40px; }}
  header {{ text-align:center; margin-bottom:20px; }}
  h1 {{ margin:0; font-size:28px; }}
  h1 span {{ color:var(--rojo); }}
  header p {{ margin:6px 0 0; color:var(--gris); }}
  a.oferta {{ display:flex; gap:14px; align-items:center; background:var(--tarjeta);
             border-radius:16px; padding:12px; margin-bottom:12px; color:inherit;
             text-decoration:none; box-shadow:0 1px 3px rgba(0,0,0,.08); }}
  a.oferta:active {{ transform:scale(.99); }}
  .foto {{ position:relative; flex:none; }}
  .foto img {{ width:96px; height:96px; object-fit:contain; border-radius:10px; background:#fff; display:block; }}
  .num {{ position:absolute; top:-6px; left:-6px; background:var(--negro); color:#fff;
          font-size:13px; font-weight:700; border-radius:999px; padding:2px 8px; }}
  .info {{ min-width:0; flex:1; }}
  .titulo {{ font-size:15px; line-height:1.3; margin:0 0 6px; display:-webkit-box;
             -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }}
  .precio {{ font-size:20px; font-weight:800; }}
  .antes {{ color:var(--gris); text-decoration:line-through; font-size:14px; margin-left:6px; }}
  .pct {{ color:var(--rojo); font-weight:700; font-size:14px; margin-left:6px; }}
  .boton {{ display:inline-block; margin-top:8px; background:var(--rojo); color:#fff;
            font-weight:700; font-size:14px; padding:6px 14px; border-radius:999px; }}
  footer {{ color:var(--gris); font-size:12px; text-align:center; line-height:1.5; margin-top:24px; }}
</style>
</head>
<body>
<main>
  <header>
    <h1>El Tío <span>Ahorrador</span></h1>
    <p>Ofertas de Mercado Libre · actualizado {fecha}</p>
  </header>
{ofertas}
  <footer>
    Los precios pueden cambiar en cualquier momento; revisa el precio final en Mercado Libre.<br>
    Contiene enlaces de afiliado: si compras, el tío gana una pequeña comisión sin costo extra para ti.
  </footer>
</main>
</body>
</html>
"""


def crear_pagina(ofertas):
    """docs/index.html: lista de ofertas para el link en la bio (GitHub Pages)."""
    docs = BASE / "docs"
    (docs / "img").mkdir(parents=True, exist_ok=True)
    for viejo in (docs / "img").glob("*.jpg"):
        viejo.unlink()
    tarjetas = []
    for n, d, foto in ofertas:
        img = ""
        if foto is not None:
            mini = foto.convert("RGBA")
            mini.thumbnail((240, 240))
            fondo = Image.new("RGB", mini.size, BLANCO)
            fondo.paste(mini, mask=mini.getchannel("A"))
            fondo.save(docs / "img" / f"{n:02d}.jpg", quality=85)
            img = f'<img src="img/{n:02d}.jpg" alt="" loading="lazy">'
        precio = f'<span class="precio">{pesos(d["precio"])}</span>' if d["precio"] else ""
        if d["antes"] and d["precio"] and d["antes"] > d["precio"]:
            pct = round(100 * (1 - d["precio"] / d["antes"]))
            precio += (f'<span class="antes">{pesos(d["antes"])}</span>'
                       f'<span class="pct">-{pct}%</span>')
        tarjetas.append(
            f'  <a class="oferta" href="{html.escape(d["link"])}" target="_blank" rel="sponsored noopener">\n'
            f'    <div class="foto">{img}<span class="num">#{n}</span></div>\n'
            f'    <div class="info"><p class="titulo">{html.escape(d["titulo"])}</p>{precio}<br>'
            f'<span class="boton">Ver oferta</span></div>\n  </a>')
    (docs / "index.html").write_text(
        PAGINA.format(fecha=time.strftime("%d/%m/%Y"), ofertas="\n".join(tarjetas)),
        encoding="utf-8")
    (docs / ".nojekyll").touch()


# ---------------------------------------------------------------- main
def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # emojis en cualquier consola
    filas = leer_links()
    if not filas:
        print("links.txt está vacío: pega tus links de afiliado, uno por línea.")
        return
    SALIDA.mkdir(exist_ok=True)
    ok, ofertas, datos = 0, [], []
    generado = time.strftime("%Y-%m-%d %H:%M:%S")
    for n, fila in enumerate(filas, 1):
        print(f"[{n}/{len(filas)}] {fila['link']}")
        try:
            d = dict(fila)
            if not (fila["titulo"] and fila["precio"] and fila["imagen"]):
                web = obtener(fila["link"])
                for k in ("titulo", "precio", "antes", "imagen"):
                    d[k] = fila[k] or web[k]
            if not d["titulo"]:
                raise ValueError("no encontré el producto en la página")
            foto = None
            if d["imagen"]:
                r = requests.get(d["imagen"], headers=HEADERS, timeout=25)
                r.raise_for_status()
                foto = Image.open(BytesIO(r.content))
            destino = carpeta(n, d["titulo"])
            destino.mkdir(parents=True, exist_ok=True)
            crear_imagen(d, foto, destino / "imagen.jpg")
            fb, ig = captions(d, n)
            (destino / "facebook.txt").write_text(fb, encoding="utf-8")
            (destino / "instagram.txt").write_text(ig, encoding="utf-8")
            ofertas.append((n, d, foto))
            datos.append({"numero": n, **d, "carpeta": destino.name, "generado": generado,
                          "facebook": fb, "instagram": ig})
            aviso = "" if d["precio"] else "  (sin precio: revísalo)"
            print(f"   ✓ {d['titulo'][:60]} — {linea_precio(d)}{aviso}")
            ok += 1
        except Exception as e:  # un producto con error no detiene a los demás
            print(f"   ✗ Error: {e}")
            print("     Puedes escribir los datos a mano en links.txt:"
                  " link | título | precio | precio antes | url de imagen")
        time.sleep(2)
    if not ofertas:
        print("\nNo se pudo leer ningún producto: no se cambió nada.")
        return 1
    # ofertas.json sirve para automatizar la publicación más adelante.
    (SALIDA / "ofertas.json").write_text(json.dumps(datos, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
    crear_pagina(ofertas)
    print(f"\nListo: {ok} de {len(filas)} publicaciones en la carpeta 'salida'.")
    print("Página del link en bio actualizada: docs/index.html")


if __name__ == "__main__":
    sys.exit(main())
