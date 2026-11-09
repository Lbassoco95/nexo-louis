# Respaldos de `spaces`: retención escalonada y freno de espacio

## Qué pasaba
`sync/hetzner-apply.sh` corre por cron cada 5 minutos. Cada vez que veía **alguna fecha de modificación más nueva** en `/opt/openclaw-sync/spaces`, hacía un snapshot completo (`tar.gz`, ~14 MB), un `rsync` y un reload. Resultado en `louis-prod`: ~288 snapshots al día, **~3 800 archivos y ~53 GB** (el techo de 14 días de retención), en un disco de 150 GB que ya estaba al 89 %.
No era un crecimiento sin freno (los de más de 14 días ya se borraban), pero sí un tercio del disco en copias casi idénticas del mismo disco.

## Qué cambia
1. **Solo hay snapshot si cambió el contenido** (`rsync --checksum` en simulación). Si solo cambiaron fechas, se da por aplicado y no se hace snapshot, rsync ni reload.
2. **Retención escalonada** (`sync/prune-backups.sh`): 24 h todos · 1–7 días uno por hora · 7–30 días uno por día · >30 días se borra · siempre los 12 más recientes. Solo toca `spaces-<14 dígitos>.tar.gz`. Estimación: de ~53 GB a ~6 GB (menos si además deja de hacer snapshot por simples fechas).
3. **Freno de espacio**: < 8 GB libres → poda de emergencia (6 h / 1 día por hora / 7 días por día); < 3 GB libres tras podar → no se aplica el delta (sin snapshot no hay reversa segura) y se reintenta después.
Probado con carpetas temporales y `rsync` real: `bash cloud/hetzner/tests/test_respaldos.sh` (35 comprobaciones).

## Aplicarlo en el servidor
1. **Diagnóstico (solo lectura):** averigua dónde vive el script que corre el cron y por qué cada corrida encuentra «algo nuevo».
   ```bash
   sudo bash <<'EOF' 2>&1 | tee /tmp/recon-backups.txt
   echo "== cron"; cat /etc/cron.d/louis-sync 2>/dev/null; sudo -u polo crontab -l 2>/dev/null | grep -i "hetzner-apply"
   echo "== log de sync (últimas líneas)"; tail -n 12 /var/log/louis-sync.log | cut -c1-160
   echo "== cuántas veces aplicó"; grep -c "aplicando delta" /var/log/louis-sync.log
   echo "== marcador"; stat -c '%y %n' /opt/openclaw-sync/.last-apply 2>/dev/null
   echo "== archivos más nuevos en el origen del sync"; find /opt/openclaw-sync/spaces -type f -printf '%TY-%Tm-%Td %TH:%TM  %p\n' 2>/dev/null | sort | tail -8
   echo "== qué cambia entre dos snapshots consecutivos"
   A=$(ls -1t /opt/openclaw/backups/spaces-2026*.tar.gz | sed -n 2p); B=$(ls -1t /opt/openclaw/backups/spaces-2026*.tar.gz | sed -n 1p); echo "$A -> $B"
   diff <(tar -tvzf "$A" | awk '{print $3,$4,$5,$6}' | sort -k4) <(tar -tvzf "$B" | awk '{print $3,$4,$5,$6}' | sort -k4) | head -20
   echo "== espacio"; df -h /; du -sh /opt/openclaw/backups; ls -1 /opt/openclaw/backups | wc -l
   ls -1 /opt/openclaw/backups | grep -v '^spaces-[0-9]\{14\}\.tar\.gz$' | head
   EOF
   ```
   Si el `diff` muestra que cada vez cambia el mismo archivo (un estado, un latido), conviene excluirlo del origen o del sync: es lo que genera el ruido.
2. **Poner los dos scripts** (`sync/hetzner-apply.sh` y `sync/prune-backups.sh`) en la carpeta que muestre el cron (`$SCRIPT_DIR/sync/`), con el mismo dueño y `chmod +x`. Haz antes una copia del script actual.
3. **Ver qué liberaría, sin borrar:** `bash prune-backups.sh --dry-run` → «se conservarían N y se borrarían M (~X MB)».
4. **Primera limpieza** (irreversible: confirma que el resultado del paso 3 te parece bien): `bash prune-backups.sh`.
5. **Vigilar el log** (`/var/log/louis-sync.log`) 15–30 min: si siguen apareciendo snapshots cada 5 minutos, es que sí hay cambios reales de contenido: revisa qué cambia (paso 1) y arréglalo en el origen.

## Lo que esto NO resuelve
- **Los snapshots viven en el mismo disco**: sirven para revertir un sync malo, no para una falla del disco o del servidor. Para eso hace falta una **copia fuera del servidor** (por ejemplo `rclone` a un Hetzner Storage Box, Wasabi o S3, una vez al día; ya está sugerido en `runbook.md`). Requiere una cuenta y credenciales.
- Otros consumidores del disco (`/opt/openclaw/legal/dof`, 16 GB, y el resto) siguen igual; con el disco en 89 % conviene una alerta de espacio (por ejemplo al Telegram que ya usa Louis) a partir de 85 %.
- No se probó en el servidor real: la primera corrida debe hacerse con `--dry-run`.
