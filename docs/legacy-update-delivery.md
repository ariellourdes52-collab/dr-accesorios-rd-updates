# Legacy update delivery: DR Accesorios RD 1.6 and 1.7

## Why this exists

Versions 1.6 and 1.7 predate the Firestore `devices` registry used by the
version-aware release sender. They cannot be safely targeted per installation
from the release workflow because the server does not have a stored FID/token
for those app instances.

Do **not** replace this with a notification+data send to the shared
`blog_updates` topic. A background notification payload may be displayed by
Android before app code can filter by installed version, so newer/current
versions could receive an incorrect update alert.

Firebase Notifications composer supports user segments by **App version**;
that targeting is not available through the FCM HTTP v1 server API. This is the
safe legacy path.

## Release procedure for 1.6 and 1.7

After the normal release workflow has:
1. published the GitHub release,
2. verified the permanent APK URL,
3. deployed and verified `version.json`, and
4. completed the version-aware per-installation FCM sender,

create two Firebase Notification campaigns, one targeting app version **1.6**
and one targeting app version **1.7**.

Use:

- Title: `Nueva versión de DR Accesorios RD`
- Body: `La versión <TARGET_VERSION> ya está disponible. Toca para actualizar.`
- Android app: DR Accesorios RD (`com.draccesoriosrd.app`)
- User segment: App version equals `1.6` (first campaign) / `1.7` (second)
- Delivery: Send now
- Expires / TTL: 24 hours
- Sound: enabled
- Custom data: **leave empty intentionally**

The 1.6/1.7 campaign is notification-only. Its only job is to make Android
display the update alert while the old APK is in the background. When the user
taps it, the launcher opens and the update checker that already exists in those
versions checks the live `version.json`. Avoiding custom `app_update` data
also avoids invoking the old background update handler unnecessarily when the
app happens to be in the foreground.

Do not target "All users", the shared `blog_updates` topic, or an Analytics
audience that contains newer versions.

## Why the notification payload is intentional

For Android apps in the background, notification messages are displayed by the
system tray. This avoids depending on the old 1.6/1.7 `onMessageReceived()`
path, which performs an additional update check and can be delayed or not finish
while background execution is restricted.

When the user taps the notification, the app opens and its existing
`UpdateChecker` verifies the live `version.json` and presents the normal
update flow.

## Coverage and hard limits

- 1.8+ registered installations: handled automatically by
  `scripts/send_update_notifications.py`.
- 1.6/1.7: handled by version-segmented Notifications composer campaigns.
- Notifications disabled by the user: no server-side method can override the
  Android notification permission.
- Force-stopped app / platform restrictions: delivery can still be limited by
  Android/Google Play services behavior.
