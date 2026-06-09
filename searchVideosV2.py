#!/usr/bin/env python3
"""
Busca videos en YouTube por nombre de persona y guarda las transcripciones disponibles usando yt-dlp.
python ./searchVideosV2.py "Jose Elias Navarro" -n 5 -o "/Users/cartagenacorp/Desktop/obsidian/raw/Jose_Elias_Navarro" -l "es,en"
python ./searchVideosV2.py "https://www.youtube.com/@Jose_Elias_Navarro" -n 0 -o "/Users/cartagenacorp/Desktop/obsidian/raw/Jose_Elias_Navarro" -l "es,en"
pip install yt-dlp
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import random
from pathlib import Path

import yt_dlp


def slugify(text: str) -> str:
    # Si es una URL de YouTube, intentamos extraer el nombre del canal o usuario
    if "youtube.com/@" in text:
        text = text.split("youtube.com/@")[-1].split("/")[0]
    elif text.startswith("http"):
        # Extraer la última parte de la URL
        text = text.rstrip("/").split("/")[-1]

    text = text.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[-\s]+", "_", text)
    return text[:80] or "persona"


def buscar_videos(nombre: str, max_resultados: int, cookies_from_browser: str = None) -> list[dict]:
    if nombre.startswith("http://") or nombre.startswith("https://"):
        # Si es un canal genérico sin pestaña, forzamos la pestaña /videos para que no traiga las listas de reproducción del canal
        if any(x in nombre for x in ["youtube.com/@", "youtube.com/channel/", "youtube.com/c/"]):
            pestañas = ["/videos", "/shorts", "/streams", "/playlists", "/releases", "/podcasts"]
            if not any(p in nombre for p in pestañas):
                nombre = nombre.rstrip("/") + "/videos"
        consulta = nombre
    else:
        consulta = f"ytsearch{max_resultados if max_resultados > 0 else 50}:{nombre}"
        
    opciones = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
    }
    
    if max_resultados > 0:
        opciones["playlistend"] = max_resultados
    
    if cookies_from_browser:
        opciones["cookiesfrombrowser"] = (cookies_from_browser,)

    with yt_dlp.YoutubeDL(opciones) as ydl:
        info = ydl.extract_info(consulta, download=False)

    entradas = info.get("entries") or []
    videos: list[dict] = []
    for entrada in entradas:
        if not entrada:
            continue
        video_id = entrada.get("id")
        if not video_id:
            continue
        videos.append(
            {
                "id": video_id,
                "titulo": entrada.get("title") or "(sin título)",
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "canal": entrada.get("uploader") or entrada.get("channel"),
                "duracion_seg": entrada.get("duration"),
                "fecha_subida": entrada.get("upload_date") or "00000000",
            }
        )
    return videos


def obtener_transcripcion(
    ydl: yt_dlp.YoutubeDL,
    video_id: str,
    idiomas_preferidos: list[str],
) -> tuple[str, str] | None:
    url = f"https://www.youtube.com/watch?v={video_id}"

    try:
        info = ydl.extract_info(url, download=False, process=False)
        if not info:
            return None
        
        # Unimos subtítulos manuales y automáticos
        subtitulos = {}
        if info.get("subtitles"):
            subtitulos.update(info["subtitles"])
        if info.get("automatic_captions"):
            subtitulos.update(info["automatic_captions"])

        if not subtitulos:
            return None

        # Buscar en el orden de idiomas preferidos
        idioma_detectado = None
        for lang in idiomas_preferidos:
            if lang in subtitulos:
                idioma_detectado = lang
                break
        
        # Si no encuentra ninguno de los preferidos, toma el primero que haya disponible
        if not idioma_detectado:
            idioma_detectado = next(iter(subtitulos.keys()))

        formatos = subtitulos[idioma_detectado]
        
        # Intentamos buscar el formato 'json3' que es el más fácil de limpiar de texto plano
        url_sub = None
        for f in formatos:
            if f.get("ext") == "json3":
                url_sub = f.get("url")
                break
        
        # Si no hay json3, agarramos cualquiera (vtt, srv3, etc.)
        if not url_sub and formatos:
            url_sub = formatos[0].get("url")

        if not url_sub:
            return None

        # Descargamos los subtítulos usando el cliente de yt-dlp
        with ydl.urlopen(url_sub) as respuesta:
            contenido = respuesta.read().decode("utf-8")

        # Si es formato json3 (formato interno de YouTube), lo limpiamos limpiamente
        if "events" in contenido:
            data = json.loads(contenido)
            lineas = []
            for event in data.get("events", []):
                if "segs" in event:
                    texto_segmento = "".join(seg["utf8"] for seg in event["segs"] if seg.get("utf8"))
                    texto_limpio = texto_segmento.strip()
                    if texto_limpio:
                        lineas.append(texto_limpio)
            texto_final = "\n".join(lineas)
        else:
            # Si cayó en formato VTT/SRT, removemos marcas de tiempo básicas con regex
            texto_final = re.sub(r"\d{2}:\d{2}:\d{2}[,.]\d{3} --> \d{2}:\d{2}:\d{2}[,.]\d{3}", "", contenido)
            texto_final = re.sub(r"<[^>]*>", "", texto_final)  # Quitar tags HTML si hay
            texto_final = "\n".join(line.strip() for line in texto_final.splitlines() if line.strip())

        return texto_final, idioma_detectado

    except Exception as e:
        print(f"    [!] Error al extraer con yt-dlp para {video_id}: {e}")
        return None


def descargar_audio(video: dict, carpeta_destino: Path, cookies_from_browser: str = None) -> bool:
    opciones_info = {
        "quiet": True,
        "no_warnings": True,
        "extractor_args": {"youtube": {"player_client": ["web,android"]}},
    }
    if cookies_from_browser:
        opciones_info["cookiesfrombrowser"] = (cookies_from_browser,)

    try:
        # Extraemos la información real del video para asegurar tener la fecha de subida
        with yt_dlp.YoutubeDL(opciones_info) as ydl_info:
            info = ydl_info.extract_info(video["url"], download=False)
            
        fecha = info.get("upload_date") or video.get("fecha_subida") or "00000000"
        if len(fecha) == 8:
            fecha_str = f"{fecha[:4]}-{fecha[4:6]}-{fecha[6:]}"
        else:
            fecha_str = fecha
            
        titulo_sanitizado = slugify(info.get("title") or video["titulo"])
        nombre_archivo = f"[{fecha_str}] - [{titulo_sanitizado}].mp3"
        ruta_salida = carpeta_destino / nombre_archivo
        
        if ruta_salida.exists():
            print(f"    -> Audio ya descargado ({nombre_archivo}), saltando...")
            return True

        print(f"    -> Descargando audio ({nombre_archivo})...")
        opciones_audio = {
            "format": "bestaudio/bestvideo+bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "0",
            }],
            "outtmpl": str(carpeta_destino / f"[{fecha_str}] - [{titulo_sanitizado}].%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "extractor_args": {"youtube": {"player_client": ["web,android"]}},
        }

        if cookies_from_browser:
            opciones_audio["cookiesfrombrowser"] = (cookies_from_browser,)

        with yt_dlp.YoutubeDL(opciones_audio) as ydl:
            ydl.download([video["url"]])
        return True
    except Exception as e:
        print(f"    [!] Error al descargar audio para {video['id']}: {e}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Busca videos de una persona en YouTube y descarga transcripciones."
    )
    parser.add_argument(
        "nombre",
        nargs="?",
        help="Nombre de la persona a buscar",
    )
    parser.add_argument(
        "-n",
        "--max",
        type=int,
        default=5,
        help="Cantidad máxima de videos (default: 5, usar 0 para descargar TODOS)",
    )
    parser.add_argument(
        "-o",
        "--salida",
        type=Path,
        default=Path("transcripciones"),
        help="Carpeta donde guardar resultados (default: transcripciones)",
    )
    parser.add_argument(
        "-l",
        "--idiomas",
        default="es,en",
        help="Idiomas preferidos separados por coma (default: es,en)",
    )
    parser.add_argument(
        "--pausa",
        type=float,
        default=2.0,
        help="Segundos base entre peticiones (default: 2.0). Se sumará un tiempo aleatorio extra para evitar bloqueos.",
    )
    parser.add_argument(
        "--cookies-from-browser",
        help="Navegador para extraer cookies (ej. chrome, firefox, safari, edge) para evitar bloqueos de YouTube.",
    )
    parser.add_argument(
        "--audio",
        action="store_true",
        help="Descargar el audio del video en formato MP3.",
    )
    parser.add_argument(
        "--sin-texto",
        action="store_true",
        help="No descargar la transcripción de texto (útil si solo quieres el audio).",
    )
    args = parser.parse_args()

    nombre = (args.nombre or "").strip()
    if not nombre:
        nombre = input("Nombre de la persona: ").strip()
    if not nombre:
        print("Error: debes indicar un nombre.", file=sys.stderr)
        return 1

    idiomas = [i.strip() for i in args.idiomas.split(",") if i.strip()]
    carpeta_persona = args.salida / slugify(nombre)

    limite_texto = "TODOS" if args.max == 0 else args.max
    print(f"Buscando videos de «{nombre}» (máx. {limite_texto})...")
    try:
        videos = buscar_videos(nombre, args.max, args.cookies_from_browser)
    except Exception as exc:
        print(f"Error al buscar videos: {exc}", file=sys.stderr)
        return 1

    if not videos:
        print("No se encontraron videos.")
        return 0

    print(f"Encontrados {len(videos)} videos. Obteniendo transcripciones...\n")

    opciones_transcript = {
        "writeautomaticsub": True,
        "writesubtitles": True,
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
    }
    if args.cookies_from_browser:
        opciones_transcript["cookiesfrombrowser"] = (args.cookies_from_browser,)

    resultados: list[dict] = []
    with yt_dlp.YoutubeDL(opciones_transcript) as ydl_transcripciones:
        for i, video in enumerate(videos, start=1):
            print(f"[{i}/{len(videos)}] {video['titulo'][:70]}...")
            
            nombre_archivo = f"{video['id']}.txt"
            archivo_destino = carpeta_persona / nombre_archivo

            item = {**video, "texto": None, "idioma": None, "error": None}
            carpeta_persona.mkdir(parents=True, exist_ok=True)

            if not args.sin_texto:
                # Si el archivo ya existe, lo saltamos (resiliencia para reanudar)
                if archivo_destino.exists():
                    print("    -> Transcripción ya descargada, saltando...")
                    item["texto"] = "(Cacheado en disco)"
                    item["idioma"] = "n/d"
                else:
                    resultado = obtener_transcripcion(ydl_transcripciones, video["id"], idiomas)

                    if resultado:
                        texto, idioma = resultado
                        item["texto"] = texto
                        item["idioma"] = idioma
                        print(f"    OK: Transcripción ({idioma}), {len(texto)} caracteres")
                        
                        # Guardar el archivo de texto en disco de forma instantánea
                        contenido = (
                            f"Título: {item['titulo']}\n"
                            f"URL: {item['url']}\n"
                            f"Idioma transcripción: {idioma}\n"
                            f"{'-' * 60}\n\n"
                            f"{texto}\n"
                        )
                        archivo_destino.write_text(contenido, encoding="utf-8")
                    else:
                        item["error"] = "sin_transcripcion"
                        print("    NO: Sin transcripción disponible")
            
            if args.audio:
                descargar_audio(video, carpeta_persona, args.cookies_from_browser)

            resultados.append(item)
            
            # Actualizar el índice al vuelo
            indice = {
                "persona": nombre,
                "procesados": len(resultados),
                "total_videos": len(videos),
                "con_transcripcion": sum(1 for t in resultados if t.get("texto")),
                "videos": resultados,
            }
            (carpeta_persona / "indice.json").write_text(
                json.dumps(indice, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            if i < len(videos):
                pausa_real = args.pausa + random.uniform(1.0, 3.0)
                time.sleep(pausa_real)

    ok = sum(1 for r in resultados if r.get("texto"))
    print(f"\nListo. {ok}/{len(videos)} con transcripción.")
    print(f"Archivos en: {carpeta_persona.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())