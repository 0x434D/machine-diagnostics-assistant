"""Every number in the plant is configuration, never a constant buried in code (§10.3)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True)
class ClockConfig:
    history_depth: timedelta
    catchup_speed: float

    # §3.2 assumed the worst boot time was an evening one (~24-25 h back to the last
    # completed night shift). The true supremum is a boot approaching 06:00 itself:
    # the last *completed* night shift is then the one before that -- the ~24 h day
    # gap back to it, plus that night's own length, which peaks at 9 h instead of the
    # usual 8 on the autumn DST Sunday (2026-10-25). 24 h + 9 h = 33 h, approached but
    # never quite reached as boot -> 06:00 that day. Confirmed by
    # test_the_default_depth_covers_every_boot_time_in_the_year, which probes each
    # day's 06:00 boundary rather than an hourly grid (a grid samples every day's
    # interior and misses the true max, which sits at the reset point).
    #
    # This is a plain class constant, not a dataclass field: no type annotation, so it
    # does not become a constructor parameter (see Settings.history_depth_hours below,
    # which derives its default from this one instead of restating the number).
    DEFAULT_HISTORY_DEPTH = timedelta(hours=33)


# pydantic's own metaclass takes `**kwargs: Any` in its __new__, which mypy's strict
# disallow_any_explicit attributes to the *subclass* statement below -- reproduces on
# `class Foo(BaseModel): pass` with no fields of ours at all, so there is no code-level
# fix here. Left as a single, named exception rather than a config-wide disable because
# this is the first pydantic model in the project; if disallow_any_explicit needs the
# same exception at every future Pydantic row model (docs/ENGINEERING.md's plan for the
# analysis service), move it to a per-module mypy.ini override instead of repeating this.
class Settings(BaseSettings):  # type: ignore[explicit-any]
    model_config = SettingsConfigDict(env_prefix="PLANT_", env_file=".env")

    # clock — default derives from ClockConfig.DEFAULT_HISTORY_DEPTH so the two
    # spellings of the same number (a timedelta here, hours as a float for env-var
    # ergonomics) cannot drift apart; main() converts this back to a timedelta
    # when it builds a ClockConfig.
    history_depth_hours: float = ClockConfig.DEFAULT_HISTORY_DEPTH / timedelta(hours=1)
    # 700x keeps catch-up to ~170 s wall (33 h / 699) for the 33 h depth default,
    # inside R1's <=180 s budget with the depth floor intact; 600x would need ~198 s
    # for the same depth. Task 10 measures the real catchup_wall; Task 17 records
    # what was measured, same as the depth default above.
    catchup_speed: float = 700.0

    # line
    takt_seconds: float = 6.0
    seed: int = 20260912
    # Additive Gaussian jitter on the takt written to TaktTime, ~0.8 % of a 6 s takt.
    # Not §3.5's noise model (that arrives in M2) -- this exists so TaktTime is
    # historised at all: asyncua's monitored-item filter drops a notification
    # whenever the written value is unchanged, and a bare constant takt (M1, with no
    # noise model yet) means only the very first write is ever historised. See
    # station_s3._next_takt.
    takt_jitter_sigma: float = 0.05

    # §3.1's three line defaults. Buffer capacity is the one that matters: it sets how
    # long propagation takes to become visible, and Task 12's authenticity proof
    # measures exactly the delay it produces (5 x 6 s ~= 30 s from S2 stopping to S3
    # starving). Changing it changes that proof's expected value, which is why the
    # proof derives the number rather than hardcoding 30.
    #
    # carrier_count is provisional, and was raised from 12 on measurement. No station
    # holds a carrier between cycles, so every carrier in the line parks in a buffer
    # against 15 slots; at 12 the steady-state margin is one carrier, and
    # takt_jitter_sigma below is what closes it. Over 40,000 steps at the takts above,
    # free running with no fault injected: with jitter off the pool never emptied, and
    # at the configured sigma it pinned at 0 and S1 suspended on `carrier-return` for
    # 145 of its 508 suspended cycles (29 %). That is the plant inventing an upstream
    # supply fault nobody asked for, and since §5.4 categorises a propagation chain by
    # direction, it makes S1's reason flip between `blocked` and `starved` on a noise
    # setting. At 18 -- the 15 slots plus headroom -- carrier-return did not occur at
    # any sigma from 0.05 to 1.0. Measured against the fake stations of Task 4's
    # tests; Task 12 confirms it against the real ones.
    carrier_count: int = 18
    buffer_capacity: int = 5

    # §3.1: the stations do NOT share one takt. S3 is the slowest and paces the line
    # at the 6 s every other number is quoted against; S1 and S2 run faster so their
    # buffers fill, and S4 matches S3 so B3_4 stays near empty without S4 starving on
    # every single cycle.
    #
    # A balanced line would make buffer capacity bound nothing -- every buffer would
    # oscillate between empty and one, because each station consumes exactly as fast
    # as the one above produces. The bottleneck is what gives a buffer a level to
    # hold, and the level is what makes propagation delayed rather than immediate.
    #
    # Keyed by §4.1's station browse names, because that is what `StationNodes.code`
    # carries and what `Station._nominal_takt` looks this up with. A short "S1" here
    # misses, and a miss is the balanced line the paragraph above rules out -- so
    # `Station.__init__` refuses a station this does not name rather than falling back
    # to takt_seconds. That applies to PLANT_STATION_TAKT_SECONDS too, which is the
    # spelling a deployment can still get wrong after the default is right.
    #
    # Starting values, confirmed by Task 12's measurement rather than assumed.
    station_takt_seconds: dict[str, float] = {
        "S1_Feeding": 5.70,
        "S2_Joining": 5.85,
        "S3_Inspection": 6.00,
        "S4_Outfeed": 6.00,
    }

    # §4.1's two S2 process signals. Nominal values only -- M2c's scenario 3 drifts
    # the force down from here, and M2b replaces both with the force-distance curve
    # they summarise (§3.4a).
    joining_force_nominal: float = 4200.0  # newtons
    joining_distance_nominal: float = 12.5  # millimetres
    # Part-to-part spread around those nominals, ~1 % of force and ~0.2 % of distance.
    # Not measured -- §3.5's noise model is M2b's, and these exist so the two signals
    # vary at all (a constant is coalesced away before the historian sees it, the same
    # reason takt_jitter_sigma above exists). They are settings rather than literals
    # because M2c's scenario 3 drifts the force against exactly this spread: a drift
    # smaller than the noise it hides in is not detectable, and that ratio has to be
    # tunable to make the scenario provable either way.
    joining_force_sigma: float = 40.0  # newtons
    joining_distance_sigma: float = 0.02  # millimetres

    # §4.1's three fill levels -- S1's two feeder lanes and S4's outfeed. Each is a
    # sawtooth: drawn down (or filled up) by production, reset when an operator
    # intervenes. The shape carries no diagnosis in M2a; it exists so these are real
    # varying floats. Settings rather than literals because M2c's scenario 5
    # contaminates one lane and scenario 2 blocks the outfeed, and both scenarios are
    # written against the level a station is supposed to sit at.
    #
    # A lane holds 100 units and each part draws half of one, so a lane lasts 200 of
    # the parts it supplies -- long enough that the sawtooth is a slow trend against
    # the takt rather than a sensor that looks broken.
    lane_capacity: float = 100.0
    lane_draw_per_part: float = 0.5
    # Measurement noise on the level, not variation in the level itself.
    lane_fill_sigma: float = 0.4
    # Parts, not units: the outfeed holds whole parts and an operator clears it.
    outfeed_capacity: int = 50
    outfeed_fill_sigma: float = 0.3

    # catch-up pacing -- asyncua's own per-monitored-item notification queue caps at
    # 10,000 and silently discards the oldest entry past that, so generate_history
    # must give the ~10 ms publish loop a chance to drain before any one stream's
    # backlog gets there. 500 parts is comfortably under the cap even though every
    # part now writes a distinct TaktTime (see takt_jitter_sigma); 0.05 s matches
    # what was measured to drain a batch that size. See
    # station_s3.generate_history.
    catchup_batch_size: int = 500
    catchup_batch_pause_seconds: float = 0.05

    # inspection — M1's reject rate is a measurement knob for R4, not §3.5's
    # 1.5 % noise floor, which arrives with the noise model in M2.
    reject_rate: float = 0.05
    # R4 measurement (measurements/r4-image-sizes.txt): at compress_level=1 with the
    # sensor noise below, 320x240 clears OPC UA's MaxBufferSize (65,535 B) with margin
    # while staying inside the ~170 s catch-up wall; 640x480 does not (either PNG
    # optimize=True or compress_level=1 blew the wall-time budget at that resolution).
    image_width: int = 320
    image_height: int = 240
    # PNG zlib level, 0-9. 1 (fastest) rather than optimize=True: optimize is where
    # catch-up's wall time went, and it buys nothing against the sensor noise below,
    # which is already incompressible. See measurements/r4-image-sizes.txt.
    image_compress_level: int = 1
    # The classifier's own /inspect response now carries the authoritative
    # model_version (PartOutcome.model_version) -- this is no longer threaded through
    # station_s3._emit_part. Kept as the value a non-HTTP stub producer can fall back
    # to, and as documentation of the deployment's expected model version.
    model_version: str = "simulated-1"

    # Budget for one /truth + /inspect round trip. httpx's own default is 5 s, which
    # catch-up's own batching can exceed on a loaded laptop; a request that outlives
    # this is a real failure and must surface as one, so it is a number to tune rather
    # than a timeout to remove.
    inspection_timeout_seconds: float = 30.0

    # How often simulator.status rewrites its snapshot. Below one takt, so `exec
    # python -m simulator.status` is never more than one part behind the ledger it
    # reports.
    status_interval_seconds: float = 5.0

    # boundary
    endpoint_url: str = "opc.tcp://line-simulator:4840/plant"
    application_uri: str = "urn:machine-agent:plant:line-simulator"
    pki_root: str = "/pki"
    history_page_size: int = 1000
    inspection_url: str = "http://inspection-service:8100"
