# DR Radar · Actividad sísmica (Apps Script)

Backend gratuito de actividad sísmica para DR Accesorios RD v2.3.1.

- Consulta el feed público de USGS mediante un activador de Apps Script cada 1 minuto.
- Solo acepta eventos que USGS identifica como República Dominicana.
- Mantiene filtros de magnitud para evitar avisos por microsismos irrelevantes.
- Publica los eventos en el feed aislado `radar-seismic-v231.json`.
- No mezcla terremotos con el `radar.json` tecnológico usado por versiones anteriores.
- Envía FCM al topic exclusivo `radar_seismic_v231`.
- No solicita ni utiliza la ubicación del usuario.
- No es un sistema de alerta temprana: informa eventos después de que la fuente los detecta y publica.

## Instalación

1. Crea o abre el proyecto de Google Apps Script.
2. En Configuración del proyecto, enlázalo con el proyecto Google Cloud/Firebase `dr-accesorios-rd`.
3. Activa la visualización de `appsscript.json`.
4. Copia `Code.gs` y `appsscript.json`.
5. Ejecuta manualmente `setupSeismicRadar`.
6. Autoriza los permisos solicitados.
7. Comprueba que exista un único activador de `seismicMinuteTick`.
8. Ejecuta `seismicHealth` y verifica baseline listo y un solo trigger.

El script usa `ScriptApp.getOAuthToken()`; no guarda una clave privada de cuenta de servicio.

## Prueba controlada

Antes de ejecutar `controlledSeismicTest`, crea temporalmente:

`DR_RADAR_ALLOW_CONTROLLED_TEST_V1 = YES`

La propiedad es de un solo uso. El aviso se identifica claramente como:

`PRUEBA DR Radar · NO ES UN SISMO REAL`

Después ejecuta `removeControlledSeismicTest`.

## Compatibilidad

Las versiones anteriores continúan consumiendo únicamente `radar.json`. El feed sísmico y el topic FCM están separados para evitar que una versión antigua interprete un sismo como una alerta tecnológica genérica.

## Desactivar

Ejecuta `disableSeismicMinuteTrigger`.
