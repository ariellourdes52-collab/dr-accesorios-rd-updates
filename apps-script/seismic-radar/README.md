# DR Radar · Actividad sísmica (Apps Script)

Backend gratuito para DR Accesorios RD v2.3.1.

- Consulta USGS cada 1 minuto.
- Filtra eventos relevantes para República Dominicana / Caribe cercano.
- Conserva las alertas tecnológicas existentes de `radar.json`.
- Publica los sismos en el mismo `radar.json`.
- Envía FCM al topic exclusivo `radar_seismic_v231`.
- No usa ubicación del usuario.
- No es un sistema de alerta temprana.

## Filtros

- hasta 250 km: M4.0+
- hasta 500 km: M4.5+
- hasta 900 km: M5.5+

## Instalación

1. Crea un proyecto en Google Apps Script.
2. Activa la visualización de `appsscript.json` en Configuración del proyecto.
3. Copia `Code.gs` y `appsscript.json`.
4. Ejecuta manualmente `setupSeismicRadar`.
5. Autoriza con la cuenta que administra el proyecto Firebase `dr-accesorios-rd`.
6. Comprueba que exista un único activador de `seismicMinuteTick` cada minuto.
7. Ejecuta `seismicHealth` para verificar estado.

El script usa `ScriptApp.getOAuthToken()`; no necesita guardar una clave privada de cuenta de servicio.

## Prueba controlada

Antes de ejecutar `controlledSeismicTest`, crea temporalmente la Script Property:

`DR_RADAR_ALLOW_CONTROLLED_TEST_V1 = YES`

La prueba se desarma automáticamente después de un uso y el aviso indica claramente:

`PRUEBA DR Radar · NO ES UN SISMO REAL`

Después ejecuta `removeControlledSeismicTest`.

## Desactivar

Ejecuta `disableSeismicMinuteTrigger`.
