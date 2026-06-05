# Almacén de Entregables — Kawiil

Directorio compartido entre Cowork y Louis. En producción vive en:
`/opt/openclaw/entregables/` (Hetzner).

## Convención de archivos

Cada entregable es un archivo Markdown con frontmatter YAML:

```
YYYY-MM-DD-slug-del-titulo.md
```

### Estados válidos

| Estado | Significado |
|--------|-------------|
| `borrador` | En elaboración en Cowork |
| `listo` | Terminado, pendiente de VoBo |
| `en_vobo` | Enviado a dirección para aprobación |
| `aprobado` | VoBo dado; entregable vigente |
| `archivado` | Reemplazado o ya no activo |

### Frontmatter mínimo

```yaml
---
titulo: Perfiles de puesto Joshui
cliente: Kawiil
estado: listo
responsable: Cowork
fecha_creacion: 2026-06-01
fecha_actualizacion: 2026-06-04
vobo: pendiente
---
```

## Subdirectorios

- `_briefs/` — Briefs de dispatch preparados por Louis para Cowork.
  Formato: `YYYY-MM-DD-brief-slug.md`. Louis los genera con `dispatch_preparar_brief`.

## Reglas

1. No borrar archivos; usar estado `archivado`.
2. Actualizar `fecha_actualizacion` en cada cambio de estado.
3. El campo `vobo` puede ser: `pendiente`, `aprobado`, `rechazado`.
4. El análisis va en el cuerpo del archivo; el frontmatter es solo metadatos.
5. Archivos que empiezan con `_` son internos y los ignoran las herramientas de listado.
