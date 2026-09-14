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
    # extra="ignore", against pydantic-settings' own default of "forbid", because
    # `plant/.env` has two consumers and only one of them is this class. Compose reads
    # the same file for interpolation, and the keys it needs there are not this
    # object's: HOST_UID and HOST_GID decide the uid every plant container runs as, and
    # PLANT_HMI_PORT is the *host* port the screen is published on -- not
    # hmi_server_port, which is where the server binds inside the container.
    #
    # Under "forbid", all three abort `Settings()` with "Extra inputs are not
    # permitted", which means `make check` dies during collection for anyone who did
    # what `.env.example` line 1 and `make preflight`'s own hint tell them to do. It is
    # not a quirk of the unprefixed pair either: PLANT_HMI_PORT carries the prefix and
    # is rejected exactly the same way, so the prefix does not partition this file and
    # "forbid" was never the right rule for it.
    #
    # The cost, stated rather than discovered later: a mistyped PLANT_* key -- the
    # PLANT_STATION_TAKT_SECONDS spelling the comment below worries about, say -- is now
    # silently ignored and the default is used. That is a real loss. It is not
    # separable from the gain: after the dotenv source runs, a typo'd `plant_takt_second`
    # and a legitimate `plant_hmi_port` are both just extra keys, indistinguishable to
    # anything downstream. Catching typos needs a check against the raw file, which is a
    # different guard from this one.
    model_config = SettingsConfigDict(
        env_prefix="PLANT_", env_file=".env", extra="ignore"
    )

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
    # stations.base.Station.next_takt.
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

    # How far apart two PackML states published for the same station sit on the
    # simulated timeline. Not cosmetic: §5.2 keys `state_changes` on
    # `(station_id, source_ts)`, so states sharing an instant are one row in Postgres
    # and only the last survives -- a bring-up would arrive as `Execute` with nothing
    # before it, and a hold as `Held` with no `Holding`.
    #
    # 0.5 s puts a station's whole bring-up (BRING_UP_TRANSITIONS = 6, so 3 s) inside
    # the one-takt gap between the historian's priming row and the first cycle, with
    # half the gap spare; `line.run_catchup` refuses a value that does not fit rather
    # than publishing state history over the top of production.
    state_transition_seconds: float = 0.5

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

    # §3.4a's force-distance curve, and the two knobs that have to move independently
    # or M2c's scenario 7 is unwinnable by construction. Scenario 7 is a bad component
    # lot with a *perfectly stable* joining force; scenario 3 is a force drift; both
    # raise `gap` defects, so the curve's shape is the only thing separating them.
    #
    # The press seats the part a fixed depth past wherever it first meets resistance.
    # That is what makes the peak (stiffness x seat depth) independent of the contact
    # point, which is what scenario 7 needs. The alternative -- a press that always
    # stops at the same absolute position -- makes a late contact point *lower* the
    # peak, which is scenario 3's symptom showing up inside scenario 7, and §3.5 rules
    # it out by saying scenario 7's joining force is perfectly stable.
    #
    # 40 samples is §3.4a's "a few dozen values per part"; against the 20 mm window
    # below that is a sample every 0.51 mm, so ~9 of them land on the 4.5 mm rise.
    curve_samples: int = 40
    # The recorded window. Wide enough for a contact point well past the 8.0 mm nominal
    # plus the seat depth; a scenario that pushes contact past stroke - seat depth is
    # refused rather than silently clipped, because a clipped curve reports a peak the
    # press never reached.
    curve_stroke_mm: float = 20.0
    # How far the press travels past contact. Together with joining_force_nominal it
    # fixes the nominal stiffness, and with joining_distance_nominal the nominal contact
    # point -- both derived below rather than restated, so §4.1's two published scalars
    # and the curve that summarises them cannot drift apart.
    press_seat_depth_mm: float = 4.5
    # Sensor noise on the idle part of the stroke, ~0.2 % of the nominal peak. Not
    # joining_force_sigma, which is part-to-part spread of the peak; this is the noise
    # floor within one part's trace. It applies below contact only: the samples after
    # contact carry the part-to-part variation through the two knobs themselves, and
    # noise on them would blur exactly what scenario 7 has to separate. It must also
    # stay far below curve_fit_band_low x peak (840 N at the nominals) or the rise fit
    # would take in samples that are not on the rise.
    curve_noise_sigma: float = 8.0
    # Which part of the rise `contact_of` and `distance_of` fit their line through, as
    # a fraction of the peak. Below the low edge sit the noise floor and the sample that
    # straddles contact; above the high edge sits the knee where the press reaches depth
    # and the force stops climbing. Neither end is on the straight part.
    curve_fit_band_low: float = 0.2
    curve_fit_band_high: float = 0.8

    @property
    def press_stiffness_nominal(self) -> float:
        """Newtons per millimetre of travel past contact.

        Derived, not configured: it is joining_force_nominal spread over the seat
        depth, and configuring it separately would be the same number written twice
        with nothing keeping the two spellings equal.
        """
        return self.joining_force_nominal / self.press_seat_depth_mm

    @property
    def press_contact_nominal_mm(self) -> float:
        """Where a nominal part first meets resistance.

        Derived for the same reason: §4.1's published joining distance is the whole
        travel, and the whole travel is the contact point plus the seat depth.
        """
        return self.joining_distance_nominal - self.press_seat_depth_mm

    # §3.1's identity model. Lot size sets how many parts a contaminated lot touches,
    # which is what M2c's scenario 7 containment list is scored against: too large and
    # every part is in the lot, too small and the correlation has no power. At the 6 s
    # takt above, 500 components is 500 parts on one lane -- 50 minutes of production,
    # so an 18 h history crosses roughly twenty lots per lane and a lot is a period a
    # defect rate can actually be compared across.
    #
    # supplier_count is what makes "which supplier" a question with more than one
    # answer; the lot is what containment is scored on, and the supplier is what a
    # §6.4 audit trail reports upwards.
    lot_size: int = 500
    lot_code_prefix: str = "L-"
    supplier_count: int = 3

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
    # what was measured to drain a batch that size. Re-measured at 25 streams by R5
    # (measurements/r5-streams.json: 495,000 rows, none dropped). See
    # line.run_catchup.
    catchup_batch_size: int = 500
    catchup_batch_pause_seconds: float = 0.05

    # inspection — §3.5's noise floor (M2 design D10, closing assumption A16). M1 ran
    # at 5 % as a declared measurement knob for R4.
    #
    # D10 also expected this to cut catch-up's largest cost, on the grounds that rejects
    # are the only parts that render an image. That half is wrong, and it is written down
    # here because the argument is persuasive enough to be made again:
    # `inspection_client.produce` renders *every* part, since the classifier has to be
    # given an image to classify, and the rate decides only which images are carried into
    # the OPC UA event (§3.4). Measured over two boots of this stack at 25 streams --
    # 183.1 s at 0.05 against 182.3 s at 0.015, inside the boot-to-boot spread R3 found --
    # while the renders did not change at all -- 19,799 inspection events in each boot,
    # one render behind every one of them -- and only the images *carried* fell, 939 to
    # 294 and 103 MB to 32 MB. The rate moves how much the history weighs, not how long
    # it takes to generate.
    reject_rate: float = 0.015
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
    # model_version (PartOutcome.model_version) -- it is not threaded through the
    # station. Kept as the value a non-HTTP stub producer can fall back
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

    # The plant HMI (§15), served in-process by the simulator (D5). Not a second
    # container polling the status file above: that would make status_interval_seconds
    # the screen's frame rate, and would put a second copy of the line's state between
    # the line and the screen.
    #
    # 0.5 s is below one takt at any configured station, so the screen is never a part
    # behind the line it draws. It is not below `state_transition_seconds`, so a
    # bring-up's acting states are not all individually visible -- the screen shows
    # what the line is doing now, and the historian is what holds the sequence.
    hmi_interval_seconds: float = 0.5
    # Where that server binds inside the container. 0.0.0.0 because `plant-hmi` reaches
    # it by service name over plant-net, and the container's own address on that network
    # is not knowable here. It is deliberately NOT published to the host: the simulator
    # publishes exactly one port, 4840, and test_compose_invariants pins that count.
    #
    # Named *_server_* rather than hmi_port because PLANT_HMI_PORT is the host port
    # plant/compose.yml publishes the screen on, and line-simulator reads the same .env
    # -- one spelling for two different ports is how a screen ends up proxying to
    # nothing.
    hmi_server_host: str = "0.0.0.0"
    hmi_server_port: int = 8200

    # boundary
    endpoint_url: str = "opc.tcp://line-simulator:4840/plant"
    application_uri: str = "urn:machine-agent:plant:line-simulator"
    pki_root: str = "/pki"
    history_page_size: int = 1000
    inspection_url: str = "http://inspection-service:8100"
