# Respaldos de `spaces`: retención escalonada y freno de espacio

## Qué pasaba (causa CONFIRMADA en `louis-prod`)
El cron `*/5 * * * * polo bash /opt/louis/sync/hetzner-apply.sh` hacía un snapshot completo (`tar.gz`, ~14 MB) **cada 5 minutos, para siempre**, aunque no hubiera nada nuevo: ~288 al día, **~4 300 archivos y ~53 GB** en un disco de 150 GB al 89 %. Dos cosas se combinaban:
1. **El origen del sync está vacío.** `/opt/openclaw-sync/spaces` no tiene ningún archivo (el sync desde la Mac dejó de entregar; en el log hay ~17 865 corridas, unos 62 días).
2. **Un error del script con el origen vacío.** Con cero archivos, `LATEST_MTIME` queda en blanco y la línea 41 (`(( ${LATEST_MTIME%.*} <= LAST_APPLY_MTIME ))`) falla con `syntax error: operand expected`. Ese error **no detiene el script**: la condición «no hay nada nuevo» simplemente falla y el script sigue adelante: snapshot, rsync de la nada y marcador nuevo. Cada corrida. El log lo muestra cada 5 minutos.
Por eso el `diff` entre snapshots consecutivos sale **vacío**: son copias idénticas. Reproducido en pruebas con el script viejo y un origen vacío (mismo error, un snapshot por corrida).
No es un crecimiento sin freno (los de más de 14 días ya se borraban): ~53 GB es el techo de esa retención.

## Qué cambia
0. **Arreglo de raíz:** con el origen vacío (o ilegible) `LATEST_MTIME` vale 0 y el script sale en silencio. Sin error, sin snapshot. Con prueba de regresión.
1. **Solo hay snapshot si cambió el contenido** (`rsync --checksum` en simulación). Si solo cambiaron fechas, se da por aplicado y no se hace snapshot, rsync ni reload.
2. **Retención escalonada** (`sync/prune-backups.sh`): 24 h todos · 1–7 días uno por hora · 7–30 días uno por día · >30 días se borra · siempre los 12 más recientes. Solo toca `spaces-<14 dígitos>.tar.gz`. Estimación: de ~53 GB a ~6 GB (menos si además deja de hacer snapshot por simples fechas).
3. **Freno de espacio**: < 8 GB libres → poda de emergencia (6 h / 1 día por hora / 7 días por día); < 3 GB libres tras podar → no se aplica el delta (sin snapshot no hay reversa segura) y se reintenta después.
Probado con carpetas temporales y `rsync` real: `bash cloud/hetzner/tests/test_respaldos.sh` (35 comprobaciones).

## Aplicarlo en el servidor (`/opt/louis/sync/`)
**Primero decide:** ¿todavía se usa el sync Mac → Hetzner? El origen vacío sugiere que no. Si ya no se usa, lo más limpio es **desactivar el cron** (comentar la línea de `/etc/cron.d/louis-sync`) además de aplicar el arreglo; si se va a usar, el arreglo basta.
1. **Guardar los originales:** `sudo cp /opt/louis/sync/hetzner-apply.sh /opt/louis/sync/hetzner-apply.sh.bak-$(date +%F)`
2. **Traer los dos scripts.** Si `/opt/louis` es un clon de git: `git -C /opt/louis pull` (los archivos están en `cloud/hetzner/sync/`; ajusta si la carpeta es otra). Si no, desde GitHub (el repo es público) y **verificando la huella**:
   ```bash
   cd /tmp
   R=https://raw.githubusercontent.com/Lbassoco95/nexo-louis/main/cloud/hetzner/sync
   curl -fsSL $R/hetzner-apply.sh -o hetzner-apply.sh && curl -fsSL $R/prune-backups.sh -o prune-backups.sh
   sha256sum hetzner-apply.sh prune-backups.sh      # compáralas con las que te dio Claude en el chat
   bash -n hetzner-apply.sh && bash -n prune-backups.sh && echo "sintaxis OK"
   sudo install -o polo -g polo -m 755 hetzner-apply.sh prune-backups.sh /opt/louis/sync/
   ```
3. **Ver qué liberaría, sin borrar:** `sudo -u polo bash /opt/louis/sync/prune-backups.sh --dry-run`
4. **Primera limpieza** (irreversible: confirma el resultado del paso 3): `sudo -u polo bash /opt/louis/sync/prune-backups.sh`
5. **Comprobar en 10 minutos:** `tail -n 5 /var/log/louis-sync.log` → ya **no** debe salir el error de la línea 41 ni «aplicando delta», y `ls -1 /opt/openclaw/backups | wc -l` no debe seguir subiendo.

## Lo que esto NO resuelve
- **Los snapshots viven en el mismo disco**: sirven para revertir un sync malo, no para una falla del disco o del servidor. Para eso hace falta una **copia fuera del servidor** (por ejemplo `rclone` a un Hetzner Storage Box, Wasabi o S3, una vez al día; ya está sugerido en `runbook.md`). Requiere una cuenta y credenciales.
- Otros consumidores del disco (`/opt/openclaw/legal/dof`, 16 GB, y el resto) siguen igual; con el disco en 89 % conviene una alerta de espacio (por ejemplo al Telegram que ya usa Louis) a partir de 85 %.
- No se probó en el servidor real: la primera corrida debe hacerse con `--dry-run`.
