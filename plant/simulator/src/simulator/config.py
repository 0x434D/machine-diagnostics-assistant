"""Every number in the plant is configuration, never a constant buried in code (§10.3)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta

from pydantic import field_validator
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
    # One of §3.5's two sources of takt variation, the other being `noise`'s micro-stops
    # (`stations.base.Station.next_takt` adds both).
    #
    # **It must be positive, and that is enforced below rather than assumed.** TaktTime
    # is the one stream whose variation rests on a single knob: asyncua's monitored-item
    # filter drops a notification whose value is unchanged, and with this at 0 the takt
    # is the station's nominal except on the ~0.1 % of cycles a micro-stop lands on.
    # Measured at sigma 0: **30 distinct values in 20,000 cycles, 59 historised rows
    # instead of 20,000** -- and reconciliation still passes, because `Ledger.record`
    # applies the same filter and drops the same rows. The other sigmas in this file do
    # not have this property: a zero `lane_fill_sigma` still leaves the sawtooth, a zero
    # `joining_force_sigma` still leaves `curve_noise_sigma` in the trace.
    #
    # D13 deleted the resample guard in `next_takt`, which also happened to raise on a
    # sigma of 0 -- "the obvious way someone turns jitter off", in its own words. This
    # restores that property without restoring the guard: the guard's reconciliation
    # claim was redundant (`Ledger.record` already did it) and its loud failure was not.
    takt_jitter_sigma: float = 0.05

    @field_validator("takt_jitter_sigma")
    @classmethod
    def _jitter_must_vary(cls, value: float) -> float:
        if value <= 0.0:
            raise ValueError(
                f"takt_jitter_sigma={value!r} leaves TaktTime at the station's nominal "
                "on all but the ~0.1 % of cycles a micro-stop lands on, and asyncua "
                "historises a stream only where it changes: measured at 59 rows in "
                "20,000 cycles, with the reconciliation still green because the ledger "
                "drops the same rows. Turn the noise floor down, not off"
            )
        return value

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
    # buffers fill, and S4 runs slightly faster still so B3_4 drains back towards empty.
    #
    # **S4 was 6.00 -- exactly S3's -- and that is a restoring force of zero.** B3_4 was
    # then a driftless random walk, kept near empty only by never being disturbed, and
    # §3.5's micro-stops are a disturbance: a jam at S4 adds carriers to B3_4 and a jam
    # at S3 removes them, with nothing to pull the level back either way. Measured over
    # 36,000 steady-state steps with micro-stops on: B3_4 sat full 19.5 % of the time and
    # S3 -- the bottleneck -- took `blocked:B3_4` 29 times. A bottleneck blocked a fifth
    # of the time is not the line §3.1 describes, and it is a baseline scenario 2 would
    # then have to be found against. At 5.90 the same run leaves B3_4 at 0 or 1 for
    # 83.5 % of the time and S3 blocked twice, while S4 starves on 1.5 % of its cycles --
    # the cost the old value was avoiding, and far from the "every single cycle" it read
    # as. Over 400,000 steps: S3 blocked 28 times in ~98,000 cycles.
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
        "S4_Outfeed": 5.90,
    }

    # §4.1's two S2 process signals, which are also the press's own two settings
    # (§3.4a). The press is **force-clamped at the top and position-stopped at the
    # bottom** -- the ordinary industrial arrangement: hydraulic relief (or a servo
    # clamp) caps the load, a hard mechanical stop ends the stroke, and the ram runs to
    # that stop on every part. So the peak force IS the clamp setting and the joining
    # distance IS the stop, and neither of them knows anything about the part.
    #
    # M2c's scenario 3 drifts the clamp. Nothing drifts the stop.
    joining_force_nominal: float = 4200.0  # newtons -- the clamp
    joining_distance_nominal: float = 12.5  # millimetres -- the hard stop
    # Part-to-part spread on the two, ~1 % of force and ~0.2 % of distance. The force
    # spread is the clamp's own, because a relief valve does not repeat perfectly; the
    # distance spread is the position sensor's measurement noise, because a hard stop is
    # a hard stop. Neither is measured -- they exist so the two streams vary at all (a
    # constant is coalesced away before the historian sees it, the same reason
    # takt_jitter_sigma above exists), and because scenario 3 drifts the force against
    # exactly this spread: a drift smaller than the noise it hides in is not detectable,
    # and that ratio has to be tunable to make the scenario provable either way.
    joining_force_sigma: float = 40.0  # newtons
    joining_distance_sigma: float = 0.02  # millimetres

    # §3.4a's force-distance curve. THREE knobs, and which fault moves which is the
    # whole of why the curve is stored:
    #
    #   * the clamp force above is the *press's* knob. Scenario 3 drifts it, and it is
    #     the only one of the three that moves a published scalar.
    #   * the contact point is the *components'* knob -- an undersized component lets
    #     the ram travel further before it meets resistance. Scenario 7 moves it, and it
    #     moves NEITHER published scalar: the clamp still caps the load and the ram
    #     still runs to the same stop. It is visible only in the shape of the trace.
    #   * the stiffness is the *material's*: the slope of the rise, and curve-only.
    #
    # An earlier draft of this made the peak stiffness x a fixed seating depth, so that
    # (peak, distance) and (stiffness, contact) were a bijection -- the whole curve was
    # reconstructible from the two scalars to ~1e-12 N, D6's curve table stored nothing
    # the scalar table did not, and scenario 7 collapsed into a two-scalar query that
    # points at the right answer. §3.5 calls scenario 7 the strongest test in the set
    # precisely because the symptom points at the WRONG cause, so that draft destroyed
    # the scenario. A summary is a projection and is allowed to lose information;
    # losing the contact point into the curve is what the projection is for.
    #
    # 51 samples over the 12.5 mm stroke is a reading every 0.25 mm of ram travel --
    # §3.4a's "a few dozen values per part", and enough that the fitted band below holds
    # five or six points rather than two.
    curve_samples: int = 51
    # Where a nominal part first meets resistance, as ram travel from the top of the
    # stroke. 8.0 mm of the 12.5 mm stroke leaves 4.5 mm for the part to compress in,
    # which is the headroom a scenario has to move contact later in without the clamp
    # going unreached.
    press_contact_nominal_mm: float = 8.0
    # Part-to-part spread on the contact point: component height varies. This is the
    # noise scenario 7's shift has to be significant against, so it is the number that
    # decides whether a bad lot is detectable at all -- and the reason the analysis has
    # to run a significance test rather than read one part.
    press_contact_sigma_mm: float = 0.05  # millimetres
    # How far a nominal part compresses before the clamp takes over. With the clamp it
    # fixes the nominal stiffness below; 2.0 mm of the 4.5 mm available means the clamp
    # is reached well before the stop even for a contact point 2 mm late, which is the
    # geometry `force_distance` refuses to violate.
    press_compression_mm: float = 2.0
    # Part-to-part spread on the slope, ~2 % of nominal: material hardness varies.
    press_stiffness_sigma: float = 40.0  # newtons per millimetre
    # Load-cell noise, ~0.2 % of the clamp, on **every** sample of the trace. Not
    # joining_force_sigma, which is the part-to-part spread of the clamp itself; this is
    # the noise floor within one part's trace. Applying it only to the idle part of the
    # stroke is what made the earlier draft's inversion exact, and it would leave the
    # per-part measurement with no spread exactly where §3.5 wants a significance test
    # to be necessary.
    curve_noise_sigma: float = 8.0
    # Which part of the rise `contact_of` fits its line through, as a fraction of the
    # peak. Below the low edge sit the noise floor and the sample that straddles
    # contact; above the high edge sits the knee where the clamp takes over and the
    # force stops climbing. Neither end is on the straight part.
    curve_fit_band_low: float = 0.2
    curve_fit_band_high: float = 0.8
    # How far above the noise floor the band's lower edge must sit, in sigma. Enforced
    # rather than documented: a noise sample taken for a point on the rise gives a
    # slightly wrong contact point, not an exception, and a wrong number that looks
    # right is what this module exists to prevent. At the values above the margin is
    # ~105 sigma, so this refuses only a configuration that has moved a long way.
    curve_fit_noise_margin_sigmas: float = 20.0

    @property
    def carrier_wear_factor(self) -> float:
        """Scenario 4's multiplier on a worn carrier's defect propensity.

        Derived from `carrier_wear_sigmas` and the baseline spread rather than
        configured beside them: the pair is a ratio, and a third field holding the
        product would be the same number twice with nothing keeping the two equal --
        which is exactly what would let a change to the spread leave the injected wear
        where it was and silently make scenario 4 easier or unwinnable.
        """
        return math.exp(self.carrier_wear_sigmas * self.carrier_quality_log_sigma)

    @property
    def press_stiffness_nominal(self) -> float:
        """Newtons per millimetre of compression.

        Derived, not configured: it is the clamp force reached over
        press_compression_mm, and configuring it separately would be the same number
        written twice with nothing keeping the two spellings equal.
        """
        return self.joining_force_nominal / self.press_compression_mm

    # §3.1's identity model. Lot size sets how many parts a contaminated lot touches,
    # which is what M2c's scenario 7 containment list is scored against: too large and
    # every part is in the lot, too small and the correlation has no power. At the 6 s
    # takt above, 500 components is 500 parts on one lane -- 50 minutes of production,
    # so the shipped history depth (ClockConfig.DEFAULT_HISTORY_DEPTH, 33 h) crosses
    # roughly forty lots per lane and a lot is a period a defect rate can actually be
    # compared across. Not eighteen hours and twenty lots: that was this comment until
    # the third place in this repository had to correct the same 18 h to 33 h.
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

    # faults — §3.5's six, as the magnitudes a scenario injects them at (`simulator.
    # faults`). Flat fields rather than one mapping keyed by fault name: `extra="ignore"`
    # above means pydantic cannot tell a mistyped key from another consumer's, so a
    # mis-keyed entry silently leaves every reader on the default -- which is exactly how
    # `station_takt_seconds` once gave all four stations the line-wide takt. A field name
    # is checked by mypy at every read.
    #
    # Each magnitude is an **end**, not a rate: `faults.Fault` ramps from nominal to it
    # over the matching ramp and then holds. See the module docstring for why a rate is
    # the wrong shape here.
    #
    # Starting values, in the same sense `station_takt_seconds` above carries them: the
    # scenario tasks measure each against the noise floor it has to be visible through,
    # and carrier wear is the one already expressed that way (`carrier_wear_sigmas`).
    #
    # Scenario 1. Zero, not a fraction: §3.5 calls this starvation, and a feeder running
    # slow is a different fault with a different consequence chain.
    feeder_starvation_factor: float = 0.0
    # Scenario 2. Parts of backlog added to OutfeedFill. Above outfeed_capacity (50), so
    # the level reaches the point a full outfeed is at rather than approaching it; over
    # ten minutes, so the blockage builds the way a stopped discharge conveyor does
    # instead of appearing between two cycles.
    outfeed_blockage_parts: float = 70.0
    outfeed_blockage_ramp_seconds: float = 600.0
    # Scenario 3. -420 N is 10 % of the 4200 N clamp and 10.5 x joining_force_sigma, so
    # the drifted mean sits well outside the part-to-part spread it has to be seen
    # through. Over an hour, because §3.5 calls it a drift: a step would be a different
    # failure, and scenario 7 -- where the same rising `gap` must NOT be explained by the
    # force -- is only a test at all while this one is genuinely visible in the force.
    joining_force_drift_newtons: float = -420.0
    joining_force_drift_ramp_seconds: float = 3600.0
    # Scenario 5. Six times the baseline propensity of the two classes §3.5 names for a
    # contaminated lane, applied as a step: a lane is contaminated when someone tips the
    # wrong tray into it, not over an hour.
    lane_contamination_factor: float = 6.0
    # Scenario 6. Fouling leaves 55 % of the camera's nominal contrast after two hours.
    # D8 makes the confidence fall out of the image rather than out of a knob, so what
    # this number has to be is set by the classifier's statistic and is measured where
    # that change lands.
    optics_fouling_factor: float = 0.55
    optics_fouling_ramp_seconds: float = 7200.0

    # noise floor — §3.5's permanent background (`simulator.noise`), and the one ratio
    # that decides whether this milestone's scenario 4 is findable at all.
    #
    # **Genuine carrier-to-carrier variation**, as the log-sigma of a lognormal
    # multiplier on a carrier's defect propensity. Lognormal rather than Gaussian
    # because the quantity is a positive scale; normalised to a mean of exactly 1, so it
    # redistributes the line's scrap across carriers without moving the line's own rate
    # off reject_rate.
    #
    # 0.35 is a ~36 % relative spread between carriers. It is large enough to be a
    # *property of the carrier* rather than a rounding of the binomial sampling noise
    # that any per-carrier count carries anyway -- the two are comparable at the
    # shipped depth, which is measured in test_noise rather than asserted here.
    carrier_quality_log_sigma: float = 0.35
    # **Scenario 4's wear, in multiples of that spread.** Expressed as a ratio and not
    # as a factor because the ratio is the number that decides the milestone: too tight
    # and finding carrier 7 is a GROUP BY, too loose and it is impossible, and either
    # way M3 inherits a scenario that proves nothing. `test_noise` measures where it
    # actually lands and records the effect size; the target is that carrier 7 separates
    # from the pack by clearly more than the pack's own worst member does, so that an
    # agent taking the highest carrier is wrong on a clean run and right on this one.
    carrier_wear_sigmas: float = 3.0

    # **Micro-stops** — §3.5's brief jams every ~20 min, line-wide. The interval is for
    # the line; `noise.NoiseFloor.micro_stop_seconds` divides it by the number of
    # stations, so adding a fifth station does not make the line jam more often.
    micro_stop_mean_interval_seconds: float = 1200.0
    micro_stop_min_seconds: float = 4.0
    # Below buffer_capacity x takt (5 x 6 s = 30 s), which is how long a *full* buffer
    # takes to drain: a micro-stop can therefore never on its own produce the complete
    # propagation chain Task 12's proof measures for a real stoppage. Whether it starves
    # the station below at all depends on that buffer's level at the time, which is the
    # behaviour §3.5 wants -- noise that sometimes looks like something and is not.
    micro_stop_max_seconds: float = 25.0

    # **Operator interventions.** Triangular: most alarms are acknowledged by someone
    # already at the panel and a few wait for a shift change, which a uniform draw would
    # make equally likely. Chosen as plausible, not measured -- nothing in this project
    # has measured a real operator, and a number that sounds measured and is not is
    # worse than one that says so.
    operator_ack_min_seconds: float = 15.0
    operator_ack_mode_seconds: float = 60.0
    operator_ack_max_seconds: float = 600.0
    # §3.5's "occasional unnecessary reset": roughly one intervention in twelve changed
    # nothing. Also chosen rather than measured.
    operator_unnecessary_reset_rate: float = 0.08

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
    # How many parts §3.7's strip holds, and therefore how many reject images this
    # process keeps in memory at once. A screen-width choice, not a statistical one: the
    # strip shows what just came off the line, and "how many rejects" is answered by S4's
    # RejectCount and by the diagnostics stack, not by counting thumbnails. At §3.5's
    # 1.5 % reject rate, twenty parts hold at least one reject about a quarter of the
    # time -- that is arithmetic on the configured rate, not a measurement -- and twenty
    # reject images at R4's ~110 kB median is ~2 MB, which is the worst case this bound
    # exists to cap.
    hmi_recent_parts: int = 20

    # boundary
    endpoint_url: str = "opc.tcp://line-simulator:4840/plant"
    application_uri: str = "urn:machine-agent:plant:line-simulator"
    pki_root: str = "/pki"
    history_page_size: int = 1000
    inspection_url: str = "http://inspection-service:8100"
