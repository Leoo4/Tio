#!/usr/bin/env python3
"""El Tío Ahorrador: publica en Facebook e Instagram las ofertas de salida/.

Uso:
  python publicar.py --revisar   revisa el token y las cuentas (no publica nada)
  python publicar.py --prueba    muestra qué publicaría (no publica nada)
  python publicar.py             publica la siguiente oferta pendiente (una por vez)

Antes de publicar vuelve a revisar la oferta en Mercado Libre: si el precio cambió
o el link no es de tu cuenta de afiliado, no la publica. Cada link se publica una
sola vez (para repetirlo, bórralo de salida/publicados.json).
"""
import json
import os
import sys
import time

import requests

from generar import BASE, SALIDA, obtener, pesos

CONFIG = BASE / "config_meta.json"
PUBLICADOS = SALIDA / "publicados.json"


class ErrorMeta(Exception):
    pass


# ---------------------------------------------------------------- Graph API
def graph(cfg, metodo, ruta, **datos):
    url = f"https://graph.facebook.com/{cfg['version']}/{ruta}"
    datos["access_token"] = cfg["token"]
    if metodo == "GET":
        r = requests.get(url, params=datos, timeout=60)
    else:
        r = requests.post(url, data=datos, timeout=120)
    try:
        respuesta = r.json()
    except ValueError:
        raise ErrorMeta(f"respuesta inesperada de Meta (HTTP {r.status_code})")
    if "error" in respuesta:
        e = respuesta["error"]
        raise ErrorMeta(f"{e.get('message')} (código {e.get('code')})")
    return respuesta


def cuentas(cfg):
    """Con un token de página, /me es la página de Facebook. Con un token de usuario
    (o de usuario del sistema) se busca la página en me/accounts y se usa su token."""
    campos = "id,name,instagram_business_account{id,username}"
    try:
        pagina = graph(cfg, "GET", "me", fields=campos)
    except ErrorMeta as e:
        if "(código 100)" not in str(e):  # 100 = /me no es una página
            raise
        paginas = graph(cfg, "GET", "me/accounts", fields=campos + ",access_token")["data"]
        pagina = next((p for p in paginas if p["id"] == cfg.get("pagina_id")), None)
        if pagina is None and len(paginas) == 1:
            pagina = paginas[0]
        if pagina is None:
            raise ErrorMeta("el token no tiene acceso a tu página de Facebook (o tiene "
                            "varias y falta PAGINA_ID)")
        cfg["token"] = pagina["access_token"]
    ig = pagina.get("instagram_business_account")
    if not ig:
        raise ErrorMeta(f"la página '{pagina['name']}' no tiene una cuenta de Instagram "
                        "profesional vinculada")
    return pagina, ig


def publicar_facebook(cfg, pagina_id, oferta):
    carpeta = SALIDA / oferta["carpeta"]
    with open(carpeta / "imagen.jpg", "rb") as foto:
        r = requests.post(
            f"https://graph.facebook.com/{cfg['version']}/{pagina_id}/photos",
            data={"caption": oferta["facebook"], "access_token": cfg["token"]},
            files={"source": ("imagen.jpg", foto, "image/jpeg")}, timeout=120)
    try:
        respuesta = r.json()
    except ValueError:
        raise ErrorMeta(f"respuesta inesperada de Meta (HTTP {r.status_code})")
    if "error" in respuesta:
        raise ErrorMeta(respuesta["error"].get("message"))
    return respuesta["id"]


def publicar_instagram(cfg, ig_id, foto_fb_id, oferta):
    # Instagram necesita la imagen en una URL pública: usamos la foto que
    # acabamos de subir a Facebook.
    imagenes = graph(cfg, "GET", foto_fb_id, fields="images")["images"]
    url = max(imagenes, key=lambda i: i["width"])["source"]
    contenedor = graph(cfg, "POST", f"{ig_id}/media", image_url=url,
                       caption=oferta["instagram"])["id"]
    for _ in range(30):  # hasta 5 minutos
        estado = graph(cfg, "GET", contenedor, fields="status_code")["status_code"]
        if estado == "FINISHED":
            break
        if estado in ("ERROR", "EXPIRED"):
            raise ErrorMeta(f"Instagram no aceptó la imagen ({estado})")
        time.sleep(10)
    else:
        raise ErrorMeta("Instagram tardó demasiado en procesar la imagen")
    return graph(cfg, "POST", f"{ig_id}/media_publish", creation_id=contenedor)["id"]


# ---------------------------------------------------------------- revisiones
def revisar_oferta(cfg, oferta):
    """Devuelve None si se puede publicar, o el motivo para no hacerlo."""
    web = obtener(oferta["link"])
    if cfg.get("afiliado") and (web.get("afiliado") or "").lower() != cfg["afiliado"].lower():
        return (f"el link es del afiliado '{web.get('afiliado')}', no de "
                f"'{cfg['afiliado']}'")
    if web["precio"] != oferta["precio"]:
        nuevo = pesos(web["precio"]) if web["precio"] else "no encontrado"
        return (f"el precio cambió ({pesos(oferta['precio'])} → {nuevo}); "
                "corre generar.py otra vez")
    if cfg.get("pagina"):
        for intento in range(10):  # GitHub Pages tarda 1 o 2 minutos en actualizarse
            r = requests.get(cfg["pagina"], params={"v": time.time()}, timeout=25)
            if oferta["link"] in r.text:
                break
            if intento == 0:
                print("   … esperando a que la página del link en bio se actualice")
            time.sleep(30)
        else:
            return ("la oferta no aparece en tu página del link en bio; "
                    "revisa que generar.py la haya subido a GitHub")
    return None


# ---------------------------------------------------------------- main
def leer_json(ruta, vacio):
    return json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else vacio


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    modo = sys.argv[1] if len(sys.argv) > 1 else ""
    cfg = {"version": "v26.0", **leer_json(CONFIG, {})}
    # En GitHub Actions los datos llegan como variables (el token, como secreto)
    for clave, variable in (("token", "META_TOKEN"), ("afiliado", "AFILIADO"),
                            ("pagina", "PAGINA"), ("pagina_id", "PAGINA_ID")):
        if os.environ.get(variable):
            cfg[clave] = os.environ[variable]
    if not cfg.get("token"):
        print("Falta el token de Meta: ponlo en config_meta.json o en el secreto "
              "META_TOKEN de GitHub (instrucciones en LEEME.txt).")
        return 1
    if not cfg.get("afiliado"):
        print("⚠️  config_meta.json no tiene \"afiliado\": no se revisará que los links "
              "sean de tu cuenta.")

    if modo == "--revisar":
        token_original = cfg["token"]
        pagina, ig = cuentas(cfg)
        limite = graph(cfg, "GET", f"{ig['id']}/content_publishing_limit", fields="quota_usage")
        uso = (limite.get("data") or [{}])[0].get("quota_usage", "?")
        print(f"✓ Facebook: {pagina['name']} (id {pagina['id']})")
        print(f"✓ Instagram: @{ig.get('username')} (id {ig['id']})")
        print(f"  Publicaciones por API en las últimas 24 h: {uso}")
        info = graph(cfg, "GET", "debug_token", input_token=token_original)["data"]
        if info.get("expires_at", 0) == 0:
            print("✓ El token no caduca.")
        else:
            fecha = time.strftime("%d/%m/%Y %H:%M", time.localtime(info["expires_at"]))
            print(f"⚠️  El token caduca el {fecha}: amplíalo (paso 4 del LEEME.txt).")
        return 0

    ofertas = leer_json(SALIDA / "ofertas.json", [])
    publicados = leer_json(PUBLICADOS, {})
    pendientes = []
    for o in ofertas:
        hecho = publicados.get(o["link"], {})
        if not (hecho.get("facebook") and hecho.get("instagram")):
            pendientes.append((o, hecho))
    if not pendientes:
        print("No hay ofertas pendientes. Agrega links y corre generar.py.")
        return 0

    for oferta, hecho in pendientes:
        print(f"Oferta #{oferta['numero']}: {oferta['titulo'][:60]}")
        motivo = revisar_oferta(cfg, oferta)
        if motivo:
            print(f"   ✗ No se publica: {motivo}")
            continue
        if modo == "--prueba":
            print(f"   ✓ Lista para publicar ({len(pendientes)} pendiente(s)). "
                  "Modo prueba: no se publicó nada.")
            print("\n--- Facebook ---\n" + oferta["facebook"])
            print("\n--- Instagram ---\n" + oferta["instagram"])
            return 0
        pagina, ig = cuentas(cfg)
        clave = oferta["link"]
        try:
            if not hecho.get("facebook"):
                hecho["facebook"] = publicar_facebook(cfg, pagina["id"], oferta)
                print(f"   ✓ Facebook: publicado ({hecho['facebook']})")
            if not hecho.get("instagram"):
                hecho["instagram"] = publicar_instagram(cfg, ig["id"], hecho["facebook"], oferta)
                print(f"   ✓ Instagram: publicado ({hecho['instagram']})")
        finally:  # guardamos aunque falle una red, para no publicar doble
            hecho["fecha"] = time.strftime("%Y-%m-%d %H:%M:%S")
            publicados[clave] = hecho
            PUBLICADOS.write_text(json.dumps(publicados, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        return 0  # una oferta por vez
    print("Ninguna oferta pendiente se pudo publicar.")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ErrorMeta, requests.RequestException) as e:
        print(f"✗ Error de Meta: {e}")
        sys.exit(1)
