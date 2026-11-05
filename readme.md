[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/ambv/black)
![PyPI](https://img.shields.io/pypi/v/aiopvapi)
[![codecov](https://codecov.io/gh/sander76/aio-powerview-api/branch/master/graph/badge.svg?token=83154B19T8)](https://codecov.io/gh/sander76/aio-powerview-api)
![example workflow](https://github.com/sander76/aio-powerview-api/actions/workflows/main.yaml/badge.svg)

# Aio PowerView API

A python async API for PowerView blinds written for Home-Assistant.

Have a look at the examples folder for some guidance how to use it.

## Capabilities

| Description                           | Capabilities | Primary | Secondary | Tilt | Tilt Position | Vertical | DualShade |
| :------------------------------------ | :----------: | :-----: | :-------: | :--: | :-----------: | :------: | :-------: |
| Bottom Up                             |      0       |    X    |           |      |               |          |           |
| Bottom Up Tilt 180°                   |      1*      |    X    |           | 180° | Closed        |          |           |
| Bottom Up Tilt 90°                    |      1       |    X    |           | 90°  | Closed        |          |           |
| Bottom Up Tilt 180°                   |      2       |    X    |           | 180° | Anywhere      |          |           |
| Vertical                              |      3       |    X    |           |      |               |    X     |           |
| Vertical Tilt Anywhere                |      4       |    X    |           | 180° | Anywhere      |    X     |           |
| Tilt Only 180°                        |      5       |         |           | 180° | Anywhere      |          |           |
| Top Down                              |      6       |         |     X     |      |               |          |           |
| Top Down Bottom Up                    |      7       |    X    |     X     |      |               |          |           |
| Dual Shade Overlapped                 |      8       |    X    |     X     |      |               |          |     X     |
| Dual Shade Overlapped Tilt 90°        |      9       |    X    |     X     | 90°  | Closed        |          |     X     |
| Dual Shade Overlapped Tilt 90°        |     10       |    X    |     X     | 180° | Closed        |          |     X     |
| Dual Shade Overlapped Illuminated     |     11^      |    X    |     X     |      |               |          |     X     |

\^ Capability 11 shades have the same panel functionality as capability 8 shades, plus an integrated light. Gen3 Aura light control and reported state are supported; see [Aura lighting](#aura-lighting).

## Shades

Shades that have been directly added to the API are listed below and should function correctly. In **most** cases this is identification is purely aestetic.

Shades not listed will get their features from their **capabilities**, unfortunately the json returned from the shade can sometimes be incorrect and we need to override the features for the API (and Home-Assistant) to read them correctly.

| Name                                  | Type | Capability |
| :------------------------------------ | :--: | :--------: |
| AC Roller                             |  49  |      0     |
| Aura® Illuminated Shade               |  80  |     11     |
| Aura® Illuminated Shade               |  95  |     11     |
| Aura® Illuminated Shade               |  96  |     11     |
| Banded Shades                         |  52  |      0     |
| Bottom Up                             |   5  |      0     |
| Carole Roman Shades                   |  57  |      0     |
| Curtain, Left Stack                   |  69  |      3     |
| Curtain, Right Stack                  |  70  |      3     |
| Curtain, Split Stack                  |  71  |      3     |
| Designer Roller                       |   1  |      0     |
| Duette                                |   6  |      0     |
| Duette, Top Down Bottom Up            |   8  |      7     |
| Duette Architella, Top Down Bottom Up |  33  |      7     |
| Duette DuoLite, Top Down Bottom Up    |   9  |      7     |
| Duolite Lift                          |  79  |      9     |
| Everwood Alternative Wood Blinds      |  40  |      5     |
| Facette                               |  43  |      1     |
| M25T Roller Blind                     |  42  |      0     |
| Palm Beach Shutters                   |  66  |      5     |
| Pirouette                             |  18  |      1     |
| Pleated, Top Down Bottom Up           |  47  |      7     |
| Provenance Woven Wood                 |  19  |      0     |
| Roman                                 |   4  |      0     |
| Silhouette                            |  23  |      1     |
| Silhouette                            |  72  |      1     |
| Silhouette Duolite                    |  38  |      9     |
| Sonnette                              |  53  |      0     |
| Skyline Panel, Left Stack             |  26  |      3     |
| Skyline Panel, Right Stack            |  27  |      3     |
| Skyline Panel, Split Stack            |  28  |      3     |
| Top Down                              |   7  |      6     |
| Twist                                 |  44  |      1*    |
| Vignette                              |  31  |      0     |
| Vignette                              |  32  |      0     |
| Vignette                              |  84  |      0     |
| Vignette Duolite                      |  65  |      8     |
| Vertical                              |   3  |      0     |
| Vertical Slats, Left Stack            |  54  |      4     |
| Vertical Slats, Right Stack           |  55  |      4     |
| Vertical Slats, Split Stack           |  56  |      4     |
| Venetian, Tilt Anywhere               |  51  |      2     |
| Venetian, Tilt Anywhere               |  62  |      2     |

\* No other shade are known to have this capability and currently the only way to get this functionality is by hardcoding into this API

## Development

- Install dev requirements.
- Testing is done using NOX
- Build a package: `python .\setup.py bdist bdist_wheel --universal`
- upload a package `twine upload dist/*.*`

## Changelog

### v3.4.1
- Correct erroneous naming in function naming
- Handle empty responses from gen 1 hubs
- Handle scenemembers api endpoint case sensitive
- Update automation primary key to eventid

### v3.4.0
- BREAKING CHANGE: Update SceneMembers api to use Scene ID as index over Shade ID
- Patch bad logging of errornous warning message
- Update scene tests to use fixtures

### v3.3.0
- Limit battery max to 100%
- Move co-ordinator of shades from HA to API Core
- Remove shade limiter causing issues with tilt shades

### v3.2.1
- Add type 57 (Carole Roman Shades)

### v3.2.0
- Bug fix for gen1 shades with bad data
- Fix bad close position on tilt shades
- Add hub registration capability
- Revert gen 1/2 shades to previous power type mapping
- Add retry logic to battery status requests

### v3.1.2
- Add type 72 (Silhoutte)

### v3.1.1
- Fix missed timeout blocks and handle in websession

### v3.1.0
- General docstring updates
- Handle kwargs in websessions for management of timeout internally
- Update error handling in tools
- Handle empty values and zeros better
- Add type 53 (Sonnette) and type 95 (Aura Illuminated, Roller). Light control was not available in this release; see [Aura lighting](#aura-lighting) for current support.
- Handle PowerType 11 + 12. Both are fixed and cannot be edited

### v3.0.2

- Add type 19 (Provenance Woven Wood)
- Fix Positioning for ShadeVerticalTiltAnywhere + ShadeTiltOnly (Mid only)  
- Fix logging regression on initial setup
- Fixes for ShadeVerticalTiltAnywhere + ShadeTiltOnly
- Fix tests
- Remove unneeded declerations
- Fix shade position reporting for v2 shades
- Handle empty hub data being returned

### v3.0.1

- Raw hub data updates made via defined function (`request_raw_data`, `request_home_data`, `request_raw_firware`, `detect_api_version`)
- Parse Gen 3 hub name based on serial + mac
- Find API version based on firmware revision
- Remove async_timeout and move to asyncio

### v3.0.0

- Major overhaul to incorporate gateway version 3 API.  Version can be automatically detected or manually specified.
- UserData class is deprecated and replaced with Hub.
- ShadePosition class now replaces the raw json management of shades in support of cross generational management.
- Schedules / Automations are now supported by the API
- New get_*objecttype* methods available to returned structured data objects for consistent management

### v2.0.4

- Add Type 10 - SkyLift
- Handle calls to update shade position during maintenance
- Raise error directly on hub calls instead of logger

### v2.0.3

- Add Type 26, 27 & 28 - Skyline Panels
- Force capability 1 for Type 44 - Twist
- Align class name standard

### v2.0.2

- Bug Fix to handle shades with unexpected json responses

### v2.0.1

- Invert type 3 & 4 to match api documentation from hunter douglas
- Add type 10

### v2.0.0

- Add support for all known shade types
- Fallback to shade recognition based on capability
- Clamping to prevent MIN_POSITION or MAX_POSITION being exceeded
- Code refactoring

### v1.6.19

- Add endpoints and handle 423 response
- Remove loop as argument

### 1.6.15

- Constrain aiohttp package versions.

## Gen3 shade event stream prototype

`aiopvapi.events.ShadeEventStream` reads
`/home/shades/events?sse=true&raw=true` using the caller's aiohttp session.
It yields `ShadeEvent` objects for `motion-started` and `motion-stopped`.
`ShadeEventState` merges reported positions into `PowerviewShadeData` using
the existing shade-specific position conversions. Targets remain separate
from reported positions. Older events are rejected per shade; distinct
events sharing a timestamp retain arrival order.

### Aura lighting

Aura® Illuminated Shade indexes 80, 95, and 96 (capability 11) expose `current_light` as a
`ShadeLightState` with brightness in percent and temperature in Kelvin.
The initial snapshot and subsequent motion events populate this state.
Unknown light state remains `None`; brightness zero represents off.
Events expose `current_light` and `target_light` separately, and partial
light updates preserve omitted values and panel positions. The monitor
prints both reported and target light state.

Aura shades provide
`set_light(brightness=..., temperature=..., rate_in_seconds=...)`.
All three command fields are required. For a brightness-only change, supply the
current reported temperature. For a temperature-only change, supply the current
reported brightness divided by 100. Light commands leave panel positions
unchanged and do not overwrite reported state.
Command brightness is a fraction from 0.0 to 1.0; the captured SSE events
report brightness on a 0–100 scale. Do not pass the reported percent directly
to `set_light`.
The confirmed command is `PUT /home/shades/positions?ids=<shade-id>` with
`{"positions":{"light":{"brightness":1.0,"temperature":2700,"rateInSeconds":1}}}`.
Hardware testing confirmed a successful response for shade 291, followed by
SSE confirmation of its requested 2700 K state. Aura supports color temperatures from 2200 K
to 6500 K, inclusive.

With the SSE monitor running in another terminal, send a test command:

```sh
python3 -m aiopvapi.example.light powerview-g3.local 291 \
  --brightness 1.0 --temperature 2700 --rate-in-seconds 1
```

Use `--brightness 0` with the temperature and duration to turn the light off.
These commands change the selected light. The gateway rejected the earlier
body without `rateInSeconds`; transition-duration limits remain unconfirmed.

The initial SSE connection does not contain current state. Fetch
`Shades.get_shades()` before opening the stream, including on reconnect.
The gateway architect confirmed that GET returns current position and
motion events cover all initiating systems. Snapshot responses have no
ordering metadata. The GET-then-SSE sequence is not atomic, so this
prototype cannot reconstruct an entire movement occurring between those
requests. It does not assume event replay or an undocumented heartbeat.

The read-only monitor fetches the initial snapshot, prints events, and
reconnects with exponential backoff capped at 60 seconds. It sends no
movement commands and performs no periodic polling while connected:

```sh
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e . pytest pytest-asyncio
python3 -m aiopvapi.example.events powerview-g3.local
```

Press Ctrl+C to stop and close the connection. To run the focused tests:

```sh
python3 -m pytest -q tests/test_events.py
```

This is an unreleased library prototype. The companion Home Assistant
PowerView changes consume this stream API and expose Aura light entities.
Install this library branch when testing that Home Assistant branch; the
published aiopvapi 3.4.1 package does not include these additions.

## Links

---

- https://home-assistant.io/
- https://www.hunterdouglas.com/operating-systems/powerview-motorization


## Gen3 grouped position commands

Call `request.enable_position_batching()` on the shared Gen3 `AioRequest`
instance to combine concurrent shade position commands. Commands submitted in
one event-loop turn with compatible payloads are sent as one
`PUT /home/shades/positions?ids=1,2,3`. Position, tilt, and Aura light commands
use the existing shade methods and their normal unit conversions.

Primary and secondary positions and Aura brightness are compared as whole
percentages, discarding the fractional percentage. Values at most one
percentage point apart can share a request (for example, 50.2% and 51%). Every
pair of targets in a batch must satisfy this limit; 50%, 51%, and 52% cannot
all share one request. The first command's original values are sent for the
whole batch. Tilt, color temperature, transition time, other fields, and the
set of fields supplied must match exactly. Different commands for the same
shade retain their order, even when within the percentage tolerance.

Other payload differences stay separate, including Aura brightness commands
that preserve different color temperatures. Each caller receives its
own shade result; a per-shade gateway error raises `PvApiCommandError` for that
caller. Commands for the same shade retain their order. Stop commands form an
ordering barrier and are sent individually.

Batching is scoped to one request instance and is disabled by default. An
optional `asyncio.Lock` passed to `enable_position_batching(lock)` coordinates
writes with other radio operations. Call `disable_position_batching()` when
unloading the gateway to cancel queued and in-flight work. Home Assistant
enables this for Gen3 and dispatches multi-entity cover/light actions
concurrently. Separately awaited actions do not form a batch. SSE remains the
source of confirmed position and light state.


## Home Assistant support registration

During every Gen3 config-entry setup, including existing installs and reloads,
Home Assistant calls `hub.register_integration("HA")`. This sends
`PUT /home/integration/HA` with no request body, registering the
support tag required for gateway REST API use. An empty HTTP 204 response is
success for this operation; other calls retain their existing maintenance
response handling. The exact gateway-defined
`HA` identifier is accepted alongside the existing third-party
identifier naming rules. Registration failures use Home Assistant's normal
setup retry path. Gen1 and Gen2 hubs do not receive this request, and unloading
the integration does not remove the support tag.
