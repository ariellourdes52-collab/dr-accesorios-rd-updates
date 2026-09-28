# DR Radar SeedLink — Railway backup

Production backup of the Railway worker used for the DR Radar seismic fast path.

## Version

- Package: `DR_Radar_Railway_v1.0.zip`
- SHA-256: `1142f131d62cc33e1bbf254a9e4287c49e413364713731b507ed9a22cd236ba5`
- Archived: 2026-09-28
- Runtime: Railway background worker
- Pipeline: EarthScope SeedLink -> 5-station + DR-anchor detector -> EMSC + USGS corroboration -> signed Apps Script bridge

## Security

No HMAC secret is committed here.

Railway must provide:
- `DR_RADAR_BRIDGE_URL`
- `DR_RADAR_BRIDGE_SECRET`

Apps Script controls `DRY_RUN` vs `LIVE` through its own script property.

## Restore

Download the ZIP, extract it, configure the two Railway variables above, and deploy the extracted folder.

Verify the package SHA-256 before restoring.
