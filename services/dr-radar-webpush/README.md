# DR Radar Web Push

Servicio aislado para suscripciones Web Push de DR Radar.

- Lee únicamente los feeds públicos de DR Radar.
- No modifica el detector sísmico, Firebase Hosting ni DR Audio.
- Guarda suscripciones en SQLite sobre volumen persistente Railway.
- Inicializa una línea base al arrancar para no reenviar alertas ya activas.
- Envía Web Push estándar VAPID a suscriptores cuando detecta una alerta nueva.
