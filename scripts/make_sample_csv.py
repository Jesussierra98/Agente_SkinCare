"""Genera `catalog/sample_feeder.csv`: un CSV SINTÉTICO con los problemas típicos del PIM.

No es el catálogo real. Sirve para ejercitar el ETL (codificación, HTML, precios, clasificación).
"""

from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "catalog" / "sample_feeder.csv"

HEADER = "SKU,Nombre,Marca,Funcion,Tipo de piel,Precio,Beneficios,Ingredientes,Modo de uso,Imagen,URL,Detalle"


def row(*cells: str) -> str:
    return ",".join('"' + c.replace('"', '""') + '"' for c in cells)


def mojibake(text: str) -> str:
    """Texto UTF-8 leído como Windows-1252 (como lo deja un PIM mal exportado)."""
    return text.encode("utf-8").decode("cp1252", errors="replace")


lines = [
    HEADER,
    # Limpio, con HTML en Beneficios.
    row("000375947", "Gel limpiador suave", "clinique ", "Gel Limpiador", "Grasa", "$520.00",
        "<ul><li>Limpia sin resecar</li><li>Ayuda a reducir brotes</li></ul>", "Agua, Glicerina",
        "<p>Aplica sobre piel húmeda.</p><p>Enjuaga.</p>", "https://img.example/1.jpg", "https://ultrafemme.example/p/1", "<b>Nuevo</b>"),
    # Mojibake doble (UTF-8 leído como cp1252).
    row("000375948", mojibake("Sérum de niacinamida"), "LANCOME", "Suero antimanchas", "Todo tipo de piel", "1,249.50",
        mojibake("Unifica el tono<br/>Minimiza poros"), "Niacinamida", "Usa de noche", "", "", ""),
    # Hidratación con precio en formato con MXN.
    row("000375949", "Crema de día hidratante", "ISDIN", "Crema de dia", "Seca", "899 MXN",
        "Hidratación de 24 horas", "Ceramidas", "Mañana y noche", "", "", ""),
    # Protección solar; Funcion con espacios y mayúsculas.
    row("000375950", "Protector solar FPS 50", "La Roche-Posay", "  PROTECTOR SOLAR FLUIDO ", "Mixta", "$ 790.00",
        "Protección de amplio espectro", "Filtros UVA/UVB", "Reaplica cada 2 horas", "", "", ""),
    # SKU con ceros a la izquierda repetido: se conserva la última fila.
    row("000375950", "Protector solar FPS 50 (actualizado)", "La Roche-Posay", "Protector solar fluido", "Mixta", "$ 810.00",
        "Protección de amplio espectro", "Filtros UVA/UVB", "Reaplica cada 2 horas", "", "", ""),
    # Funcion sin mapear.
    row("000375951", "Perfume de prueba", "ACME", "Fragancia", "", "1500", "", "", "", "", "", ""),
    # Precio inválido.
    row("000375952", "Producto sin precio", "ACME", "Gel limpiador", "", "consultar", "", "", "", "", "", ""),
    # Precio fuera de rango.
    row("000375953", "Producto carísimo", "ACME", "Gel limpiador", "", "1000000.00", "", "", "", "", "", ""),
    # SKU vacío.
    row("", "Sin SKU", "ACME", "Gel limpiador", "", "100", "", "", "", "", "", ""),
    # Etiqueta anidada que forma otra al quitar la primera.
    row("000375954", "Mascarilla", "NOVE", "Mascarilla", "Todo tipo de piel", "350", "<<b>p>Hidrata</p>", "", "", "", "", ""),
]
text = "\n".join(lines) + "\n"

# Archivo en Windows-1252 (como un export de Excel), con un byte inválido en una fila final.
data = text.encode("cp1252", errors="replace")
data += b'"000375955","Fila da\x81ada","ACME","Gel limpiador","","100","","","","","",""\n'
OUT.write_bytes(data)
print(f"escrito {OUT} ({len(data)} bytes)")
