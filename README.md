# script_jira_CSV

Exporta los issues de Jira Cloud a archivos CSV (una fila por issue, con todos sus campos,
comentarios, adjuntos y registros de trabajo).

## Requisitos

- Python 3 (no necesita instalar librerías adicionales).
- Un correo de Jira y un API token de Atlassian (se crea en https://id.atlassian.com/manage-profile/security/api-tokens).

## Cómo ejecutarlo

```bash
export JIRA_EMAIL="correo"
export JIRA_API_TOKEN="el_valor_de_token"
python3 Serializar_csv.py
```

Reemplaza `correo` por tu correo de Jira y `el_valor_de_token` por tu API token.

## Opciones

| Comando | Qué hace |
|---|---|
| `python3 Serializar_csv.py` | Exporta todos los issues a CSV |
| `python3 Serializar_csv.py --campos` | Solo muestra el id y el nombre de cada campo de Jira |

## Resultado

Los archivos se guardan en la carpeta `export_jira/` como `issues_jira_001.csv`, `issues_jira_002.csv`, etc.
Cada archivo tiene como máximo 1000 filas; al llegar al límite se crea el siguiente.

Por defecto el modo es `agregar`: si vuelves a correr el script no repite los issues ya guardados.

## Configuración

Al inicio de [Serializar_csv.py](Serializar_csv.py) puedes cambiar:

- `BASE_URL`: la URL de tu Jira.
- `JQL`: la consulta que decide qué issues exportar.
- `MODO`: `"agregar"` o `"sobrescribir"`.
- `MAX_FILAS_POR_ARCHIVO`: filas por archivo CSV.
- `ARCHIVOS_SEPARADOS`: si es `True`, genera además CSV aparte de comentarios, adjuntos y trabajo.

## Problemas comunes

- **Falta la variable JIRA_EMAIL / JIRA_API_TOKEN**: ejecuta los `export` en la misma terminal donde corres el script.
- **Error 401**: el correo o el token son incorrectos.
