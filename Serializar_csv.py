"""
Exporta TODOS los datos de los issues de Jira Cloud a archivos CSV.

Genera issues_jira_001.csv (en la carpeta CARPETA_SALIDA) con UNA FILA POR ISSUE:
  - una columna por cada campo con datos
  - "Comentario 1", "Comentario 2"...  -> fecha | autor | texto   (TODOS los comentarios)
  - "Adjunto 1", "Adjunto 2"...        -> fecha | autor | archivo | tamaño | enlace
  - "Registro de trabajo 1"...         -> fecha | autor | tiempo | comentario
Opcional (ARCHIVOS_SEPARADOS = True): además comentarios_jira, adjuntos_jira y
trabajo_jira con una fila por elemento, unidos al de issues por la columna "key".

Cuando un archivo llega a MAX_FILAS_POR_ARCHIVO filas, se cierra y se sigue en el
siguiente (_002, _003, ...).

Los encabezados usan el nombre real del campo en Jira (ej. "Fecha de vencimiento"),
sin el texto "Campo personalizado (...)".

Credenciales (variables de entorno):
    export JIRA_EMAIL="tu@correo.com"
    export JIRA_API_TOKEN="tu_token"

Uso:
    python3 Serializar_csv.py            -> exporta todo
    python3 Serializar_csv.py --campos   -> solo muestra el id y nombre de cada campo
"""

import base64
import csv
import glob
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ======================= Configuración =======================
BASE_URL = "https://mavi-jjtorres.atlassian.net"
JQL = "project = ec ORDER BY created ASC"
CARPETA_SALIDA = "export_jira"
MODO = "agregar"                 # "agregar" (no repite lo ya guardado) o "sobrescribir"
POR_PAGINA = 100                 # issues por petición (Jira no da más de 100 con todos los campos)
MAX_FILAS_POR_ARCHIVO = 1000     # al llegar a este número de filas se crea un archivo nuevo
INCLUIR_COLUMNAS_VACIAS = False  # True = incluir también campos que no tienen dato en ningún issue

# Campos que NO quieres en el CSV de issues (por id). Ej: {"customfield_10019", "issuerestriction"}
CAMPOS_EXCLUIDOS = {"issuerestriction"}   # interno de Jira, no aporta

# Comentarios, adjuntos y registros de trabajo van en la MISMA fila del issue:
# una columna por cada uno ("Comentario 1", "Comentario 2"... "Adjunto 1"...),
# con todos sus datos en esa celda separados por " | ".
# Si además quieres archivos aparte (una fila por comentario/adjunto), pon True:
ARCHIVOS_SEPARADOS = False

CAMPOS_SEPARADOS = {"comment", "attachment", "worklog"}   # se tratan aparte, no como campo normal

# Orden de las primeras columnas (el resto va después: campos de Jira y luego personalizados)
ORDEN_PREFERIDO = [
    "summary", "issuetype", "status", "statusCategory", "priority", "resolution",
    "project", "parent", "components", "labels", "fixVersions", "versions",
    "assignee", "reporter", "creator", "created", "updated", "resolutiondate",
    "duedate", "statuscategorychangedate", "description", "environment",
    "timetracking", "timeoriginalestimate", "timeestimate", "timespent",
    "aggregatetimeoriginalestimate", "aggregatetimeestimate", "aggregatetimespent",
    "progress", "aggregateprogress", "workratio", "issuelinks", "subtasks",
    "votes", "watches",
]


# ======================= Conexión con Jira =======================
def _auth():
    for var in ("JIRA_EMAIL", "JIRA_API_TOKEN"):
        if not os.environ.get(var):
            raise SystemExit(f"Falta la variable {var}. Usa: export {var}=\"...\"")
    datos = f"{os.environ['JIRA_EMAIL']}:{os.environ['JIRA_API_TOKEN']}"
    return "Basic " + base64.b64encode(datos.encode()).decode()


def peticion(metodo, ruta, cuerpo=None, params=None):
    """Hace una petición a la API de Jira y devuelve el JSON. Reintenta si Jira pide esperar (429)."""
    url = BASE_URL + ruta
    if params:
        url += "?" + urllib.parse.urlencode(params)
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    for intento in range(6):
        req = urllib.request.Request(url, data=datos, method=metodo, headers={
            "Authorization": _auth(),
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code == 429 and intento < 5:
                espera = int(e.headers.get("Retry-After", 5))
                print(f"  Jira pide esperar {espera}s (demasiadas peticiones)...")
                time.sleep(espera)
                continue
            if e.code == 401:
                raise SystemExit(
                    "Credenciales rechazadas por Jira (error 401).\n"
                    "Revisa JIRA_EMAIL y JIRA_API_TOKEN (el 'token' de Apidog, NO el nextPageToken).")
            raise SystemExit(f"Error {e.code} de Jira en {ruta}: {e.read().decode()[:500]}")


def verificar_credenciales():
    yo = peticion("GET", "/rest/api/3/myself")
    print(f"Conectado como: {yo.get('displayName')}")


def limpiar_nombre(nombre):
    """Quita 'Campo personalizado (...)' / 'Custom field (...)' y deja solo el nombre."""
    m = re.match(r"^\s*(campo personalizado|custom field)\s*\((.*)\)\s*$", nombre, re.I)
    return m.group(2).strip() if m else nombre.strip()


def obtener_nombres_campos():
    """Devuelve {id_campo: nombre_legible} usando la lista de campos de Jira."""
    return {c["id"]: limpiar_nombre(c.get("name") or c["id"])
            for c in peticion("GET", "/rest/api/3/field")}


def obtener_todos_los_issues():
    """Recorre todas las páginas usando nextPageToken, pidiendo todos los campos."""
    issues, token, pagina = [], None, 0
    while True:
        cuerpo = {"jql": JQL, "maxResults": POR_PAGINA, "fields": ["*all"]}
        if token:
            cuerpo["nextPageToken"] = token
        datos = peticion("POST", "/rest/api/3/search/jql", cuerpo)
        pagina += 1
        issues.extend(datos.get("issues", []))
        print(f"  página {pagina}: {len(issues)} issues descargados")
        token = datos.get("nextPageToken")
        if datos.get("isLast", True) or not token:
            if not issues:
                print("  Aviso: 0 resultados. Revisa el JQL o los permisos del usuario.")
            return issues


def lista_completa(issue, campo, lista, ruta_api):
    """La búsqueda solo trae los primeros 20 comentarios/worklogs.
    Si hay más, los pide todos al endpoint propio del issue."""
    bloque = (issue.get("fields") or {}).get(campo) or {}
    elementos = bloque.get(lista) or []
    total = bloque.get("total", len(elementos))
    if total <= len(elementos) and bloque.get("startAt", 0) == 0:
        return elementos
    todos, inicio = [], 0
    while True:
        datos = peticion("GET", ruta_api.format(id=issue["id"]),
                         params={"startAt": inicio, "maxResults": 100})
        nuevos = datos.get(lista, [])
        todos.extend(nuevos)
        inicio += len(nuevos)
        if not nuevos or inicio >= datos.get("total", 0):
            return todos


# ======================= Convertir valores a texto =======================
def texto_adf(nodo):
    """Convierte el texto con formato de Jira (type: "doc") en texto plano."""
    if isinstance(nodo, list):
        return "".join(texto_adf(n) for n in nodo)
    if not isinstance(nodo, dict):
        return ""
    tipo = nodo.get("type")
    if tipo == "text":
        return nodo.get("text", "")
    if tipo in ("mention", "emoji", "status", "date"):
        attrs = nodo.get("attrs", {})
        return attrs.get("text") or attrs.get("shortName") or attrs.get("timestamp", "")
    if tipo in ("inlineCard", "blockCard"):
        return nodo.get("attrs", {}).get("url", "")
    if tipo == "hardBreak":
        return "\n"
    contenido = texto_adf(nodo.get("content", []))
    if tipo in ("paragraph", "heading", "listItem", "codeBlock", "blockquote", "tableRow"):
        return contenido.strip() + "\n"
    if tipo in ("tableCell", "tableHeader"):
        return contenido.strip() + " | "
    return contenido


IGNORAR = {"self", "avatarUrls", "iconUrl", "id", "accountId", "expand"}


def a_texto(valor):
    """Convierte cualquier valor de Jira en texto legible para una celda."""
    if valor is None or valor == "{}":
        return ""
    if isinstance(valor, list):
        return "; ".join(str(t) for t in (a_texto(v) for v in valor) if t != "")
    if not isinstance(valor, dict):
        return valor
    if not valor:
        return ""
    if valor.get("type") == "doc":
        return texto_adf(valor).strip()
    if "outwardIssue" in valor or "inwardIssue" in valor:          # enlaces entre issues
        lado = "outward" if "outwardIssue" in valor else "inward"
        relacion = (valor.get("type") or {}).get(lado, "")
        return f"{relacion} {valor[lado + 'Issue'].get('key', '')}".strip()
    if "displayName" in valor:                                       # personas
        return valor["displayName"]
    if "value" in valor:                                             # opciones (y en cascada)
        hijo = valor.get("child", {}).get("value")
        return f"{valor['value']} > {hijo}" if hijo else valor["value"]
    if "name" in valor:                                              # estado, tipo, componente...
        return valor["name"]
    if "key" in valor:                                               # padre, subtareas
        return valor["key"]
    if "originalEstimate" in valor or "timeSpent" in valor:          # control de tiempo
        partes = [f"{et}: {valor[k]}" for k, et in (("originalEstimate", "Estimado"),
                  ("remainingEstimate", "Restante"), ("timeSpent", "Registrado")) if k in valor]
        return " | ".join(partes)
    if "percent" in valor:                                           # progreso
        return f"{valor['percent']}%"
    if "watchCount" in valor:
        return valor["watchCount"]
    if isinstance(valor.get("votes"), int):
        return valor["votes"]
    simples = {k: v for k, v in valor.items()
               if k not in IGNORAR and not isinstance(v, (dict, list)) and v not in (None, "")}
    return ", ".join(f"{k}: {v}" for k, v in simples.items())


# ======================= Escritura con archivos rotativos =======================
class CSVRotativo:
    """Escribe filas en nombre_001.csv, nombre_002.csv... cambiando de archivo al llegar al límite.
    En modo 'agregar' continúa en el último archivo y salta las filas cuya 'clave' ya existe."""

    def __init__(self, nombre, columnas, clave, modo):
        self.base = os.path.join(CARPETA_SALIDA, nombre)
        self.clave = clave
        self.archivo = None
        self.agregadas = self.saltadas = 0
        partes = sorted(glob.glob(f"{self.base}_[0-9][0-9][0-9].csv"))

        if modo == "sobrescribir":
            for p in partes:
                os.remove(p)
            partes = []

        self.existentes = set()
        if partes:
            with open(partes[0], newline="", encoding="utf-8-sig") as f:
                self.columnas = next(csv.reader(f))
            for p in partes:
                with open(p, newline="", encoding="utf-8-sig") as f:
                    filas = list(csv.DictReader(f))
                self.existentes.update(fila.get(clave) for fila in filas)
            self.num_parte = len(partes)
            self.filas_en_parte = len(filas)
            nuevas = [c for c in columnas if c not in self.columnas]
            if nuevas:
                print(f"  Aviso ({nombre}): {len(nuevas)} columnas nuevas no caben en los archivos "
                      f"existentes y se omiten. Usa MODO='sobrescribir' para incluirlas.")
        else:
            self.columnas = columnas
            self.num_parte = 0
            self.filas_en_parte = MAX_FILAS_POR_ARCHIVO   # fuerza a crear el primer archivo

    def _abrir_siguiente(self):
        if self.archivo:
            self.archivo.close()
        self.num_parte += 1
        ruta = f"{self.base}_{self.num_parte:03d}.csv"
        self.archivo = open(ruta, "w", newline="", encoding="utf-8-sig")
        self.escritor = csv.DictWriter(self.archivo, fieldnames=self.columnas, extrasaction="ignore")
        self.escritor.writeheader()
        self.filas_en_parte = 0

    def escribir(self, fila):
        if str(fila.get(self.clave)) in self.existentes:
            self.saltadas += 1
            return
        if self.archivo is None and self.filas_en_parte < MAX_FILAS_POR_ARCHIVO:
            # continuar en el último archivo existente
            ruta = f"{self.base}_{self.num_parte:03d}.csv"
            self.archivo = open(ruta, "a", newline="", encoding="utf-8")
            self.escritor = csv.DictWriter(self.archivo, fieldnames=self.columnas, extrasaction="ignore")
        elif self.filas_en_parte >= MAX_FILAS_POR_ARCHIVO:
            self._abrir_siguiente()
        self.escritor.writerow(fila)
        self.filas_en_parte += 1
        self.agregadas += 1

    def cerrar(self, etiqueta):
        if self.archivo:
            self.archivo.close()
        nombre = os.path.basename(self.base)
        archivos = (f"{nombre}_001.csv" if self.num_parte == 1 else
                    f"{nombre}_001.csv ... {nombre}_{self.num_parte:03d}.csv" if self.num_parte
                    else "(no hubo datos, no se creó archivo)")
        print(f"  {etiqueta}: {self.agregadas} filas nuevas"
              + (f", {self.saltadas} ya existían" if self.saltadas else "")
              + f" -> {archivos}")


# ======================= Armado de filas =======================
def columnas_de_issues(issues, nombres):
    """Decide qué campos van como columna y con qué encabezado."""
    con_datos = set()
    todos = set()
    for issue in issues:
        for campo, valor in (issue.get("fields") or {}).items():
            todos.add(campo)
            if a_texto(valor) not in ("", None):
                con_datos.add(campo)
    usar = (todos if INCLUIR_COLUMNAS_VACIAS else con_datos) - CAMPOS_SEPARADOS - CAMPOS_EXCLUIDOS

    def orden(campo):
        if campo in ORDEN_PREFERIDO:
            return (0, ORDEN_PREFERIDO.index(campo), "")
        if campo.startswith("customfield_"):
            return (2, int(campo.split("_")[1]), "")
        return (1, 0, nombres.get(campo, campo).lower())
    campos = sorted(usar, key=orden)

    # encabezado = nombre real; si dos campos se llaman igual se agrega el id para distinguirlos
    conteo = {}
    for c in campos:
        conteo[nombres.get(c, c)] = conteo.get(nombres.get(c, c), 0) + 1
    encabezado = {c: (nombres.get(c, c) if conteo[nombres.get(c, c)] == 1
                      else f"{nombres.get(c, c)} [{c}]") for c in campos}
    if "parent" in encabezado:
        encabezado["parent.summary"] = f"{encabezado['parent']} - resumen"
        campos.insert(campos.index("parent") + 1, "parent.summary")
    return campos, encabezado


COLS_TOTALES = ["Total comentarios", "Total adjuntos", "Total registros de trabajo"]


def fecha_corta(f):
    """'2024-06-17T10:02:34.619-0600' -> '2024-06-17 10:02'"""
    return (f or "")[:16].replace("T", " ")


def celda_comentario(c):
    return " | ".join([fecha_corta(c.get("created")), str(a_texto(c.get("author"))),
                       str(a_texto(c.get("body")))])


def celda_adjunto(a):
    return " | ".join([fecha_corta(a.get("created")), str(a_texto(a.get("author"))),
                       a.get("filename") or "", f"{a.get('size', '')} bytes", a.get("content") or ""])


def celda_trabajo(w):
    return " | ".join([fecha_corta(w.get("started")), str(a_texto(w.get("author"))),
                       w.get("timeSpent") or "", str(a_texto(w.get("comment")))])


def fila_issue(issue, campos, encabezado):
    f = issue.get("fields") or {}
    fila = {"key": issue.get("key"), "id": issue.get("id"), "self": issue.get("self")}
    for c in campos:
        if c == "parent.summary":
            valor = ((f.get("parent") or {}).get("fields") or {}).get("summary")
        else:
            valor = f.get(c)
        fila[encabezado[c]] = a_texto(valor)
    coms, adjs, trab = issue["_comentarios"], issue["_adjuntos"], issue["_trabajo"]
    fila.update(zip(COLS_TOTALES, (len(coms), len(adjs), len(trab))))
    for n, c in enumerate(coms, 1):
        fila[f"Comentario {n}"] = celda_comentario(c)
    for n, a in enumerate(adjs, 1):
        fila[f"Adjunto {n}"] = celda_adjunto(a)
    for n, w in enumerate(trab, 1):
        fila[f"Registro de trabajo {n}"] = celda_trabajo(w)
    return fila


def filas_comentarios(issue, comentarios):
    for c in comentarios:
        yield {
            "key": issue["key"], "id_comentario": c.get("id"),
            "autor": a_texto(c.get("author")), "creado": c.get("created"),
            "actualizado": c.get("updated"), "editado_por": a_texto(c.get("updateAuthor")),
            "texto": a_texto(c.get("body")),
        }


def filas_adjuntos(issue):
    for a in issue["_adjuntos"]:
        yield {
            "key": issue["key"], "id_adjunto": a.get("id"), "archivo": a.get("filename"),
            "tamano_bytes": a.get("size"), "tipo": a.get("mimeType"),
            "autor": a_texto(a.get("author")), "creado": a.get("created"),
            "url_descarga": a.get("content"),
        }


def filas_trabajo(issue, registros):
    for w in registros:
        yield {
            "key": issue["key"], "id_registro": w.get("id"), "autor": a_texto(w.get("author")),
            "inicio": w.get("started"), "tiempo": w.get("timeSpent"),
            "segundos": w.get("timeSpentSeconds"), "comentario": a_texto(w.get("comment")),
        }


# ======================= Programa principal =======================
def exportar():
    verificar_credenciales()
    os.makedirs(CARPETA_SALIDA, exist_ok=True)

    print("Leyendo nombres de los campos...")
    nombres = obtener_nombres_campos()

    print("Descargando issues...")
    issues = obtener_todos_los_issues()
    if not issues:
        return

    print("Completando comentarios y horas de los issues que tienen más de 20...")
    for n, issue in enumerate(issues, 1):
        issue["_comentarios"] = lista_completa(issue, "comment", "comments",
                                               "/rest/api/3/issue/{id}/comment")
        issue["_trabajo"] = lista_completa(issue, "worklog", "worklogs",
                                           "/rest/api/3/issue/{id}/worklog")
        issue["_adjuntos"] = (issue.get("fields") or {}).get("attachment") or []
        if n % 100 == 0:
            print(f"  ... {n}/{len(issues)} issues revisados")

    campos, encabezado = columnas_de_issues(issues, nombres)
    max_c = max(len(i["_comentarios"]) for i in issues)
    max_a = max(len(i["_adjuntos"]) for i in issues)
    max_t = max(len(i["_trabajo"]) for i in issues)
    cols_issues = (["key", "id", "self"] + [encabezado[c] for c in campos] + COLS_TOTALES
                   + [f"Comentario {n}" for n in range(1, max_c + 1)]
                   + [f"Adjunto {n}" for n in range(1, max_a + 1)]
                   + [f"Registro de trabajo {n}" for n in range(1, max_t + 1)])
    print(f"Columnas en el CSV de issues: {len(cols_issues)} "
          f"(hasta {max_c} comentarios, {max_a} adjuntos y {max_t} registros de trabajo por issue)")

    salida_issues = CSVRotativo("issues_jira", cols_issues, "key", MODO)
    if ARCHIVOS_SEPARADOS:
        salida_coment = CSVRotativo("comentarios_jira", ["key", "id_comentario", "autor", "creado",
                                    "actualizado", "editado_por", "texto"], "id_comentario", MODO)
        salida_adj = CSVRotativo("adjuntos_jira", ["key", "id_adjunto", "archivo", "tamano_bytes",
                                 "tipo", "autor", "creado", "url_descarga"], "id_adjunto", MODO)
        salida_trab = CSVRotativo("trabajo_jira", ["key", "id_registro", "autor", "inicio", "tiempo",
                                  "segundos", "comentario"], "id_registro", MODO)

    print("Guardando...")
    for issue in issues:
        salida_issues.escribir(fila_issue(issue, campos, encabezado))
        if ARCHIVOS_SEPARADOS:
            for fila in filas_comentarios(issue, issue["_comentarios"]):
                salida_coment.escribir(fila)
            for fila in filas_adjuntos(issue):
                salida_adj.escribir(fila)
            for fila in filas_trabajo(issue, issue["_trabajo"]):
                salida_trab.escribir(fila)

    print(f"\nListo. Archivos en la carpeta '{CARPETA_SALIDA}':")
    salida_issues.cerrar("Issues")
    if ARCHIVOS_SEPARADOS:
        salida_coment.cerrar("Comentarios")
        salida_adj.cerrar("Adjuntos")
        salida_trab.cerrar("Registros de trabajo")


def listar_campos():
    verificar_credenciales()
    campos = peticion("GET", "/rest/api/3/field")
    campos.sort(key=lambda c: (bool(c.get("custom")), c["id"]))
    for c in campos:
        print(f'{c["id"]:<22} {limpiar_nombre(c.get("name") or "")}')


if __name__ == "__main__":
    if "--campos" in sys.argv:
        listar_campos()
    else:
        exportar()
