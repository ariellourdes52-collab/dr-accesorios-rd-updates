# DR Radar Web Push

Servicio aislado para suscripciones Web Push de DR Radar.

- Lee los feeds públicos de DR Radar y notifica nuevas alertas.
- Conserva SQLite sobre volumen persistente Railway y línea base al arrancar.
- No modifica detector SeedLink, Firebase Hosting, DR Audio, Google Apps Script ni Android FCM.

## Propuesta EMSC preliminar (aislada, **apagada por defecto**)

Variable Railway: `EMSC_PRELIMINARY_ENABLED=true` **solo después de aprobar y verificar el despliegue**. Por defecto es `false`.

Cuando está habilitada, la nueva consulta HTTP a EMSC-RTS (FDSN Event) permite enviar **Web Push preliminar** al publicarse un sismo en República Dominicana que cumpla los mismos filtros de distancia/magnitud (250 km/M4.0, 500 km/M4.5, 900 km/M5.5). La fuente se identifica como EMSC, no USGS; los datos quedan sujetos a revisión. Las señales sin corroborar de SeedLink jamás son una fuente de notificaciones.

La integración conserva el circuito existente: al publicarse USGS en el catálogo oficial de Firebase Hosting, el Web Push de USGS se envía como **CONFIRMACIÓN USGS**, con su información. La coincidencia por tiempo, distancia y magnitud permite impedir preliminares tardíos y conservar la identificación de eventos. La primera consulta EMSC crea una línea base sin enviar alertas anteriores. El registro de eventos queda en la misma base persistente, separado de las otras funciones.

El circuito Android/NodoPin FCM **NO** se modifica aquí. Este parche cubre el Web Push de Railway; no se debe afirmar que el preliminar ya llega al tópico Android.

Pruebas: `python -m unittest discover -s tests -p 'test_radar_early_seismic.py' -v`. No usa red, credenciales ni envía notificaciones.

No habilitar sin validar conectividad real con EMSC y sus respuestas en el servicio Railway. No ejecutar ningún test con un terremoto simulado contra topics reales.
