"""§3.5's permanent noise floor: what the line does when nothing is wrong with it.

Five components, and four of them are here. The fifth -- false accepts and false
rejects -- belongs to the classifier (D7, `inspection.classifier.SimulatedClassifier`)
and is deliberately **not** rebuilt here: a real `ModelClassifier` has error rates
emergently, so a plant that also drew them would double-count them the day one drops in.
`test_the_plant_configures_no_verdict_error_rate_of_its_own` is the mechanical half of
that decision.

**Carrier-to-carrier variation is the load-bearing one.** Without it, finding scenario
4's worn carrier is a `GROUP BY` and §3.5's significance requirement is decoration; with
too much of it the scenario is unwinnable and M3 inherits a test it cannot pass. The
ratio between the two -- `carrier_quality_log_sigma` against `carrier_wear_sigmas` -- is
declared in `config` and measured in `test_noise`, because a ratio shipped unmeasured has
moved the hard part into M3 without saying so.

**Nothing here keeps RNG state.** Every draw is a pure function of the seed and the key
it is asked about, through a fresh `random.Random`, so what a carrier or a part gets does
not depend on how many others were asked about first, in what order, or from which
station -- the same property `inspection_client.InspectionClient` already rests on, and
the reason catch-up and live can be one continuous run of one line (§3.6).
"""

from __future__ import annotations

import math
import random
import statistics
import zlib

from simulator.config import Settings


def _stream(seed: int, *key: object) -> random.Random:
    """A fresh RNG for one named draw.

    crc32, never hash(): `str.__hash__` is salted per interpreter process
    (PYTHONHASHSEED), so a hash-derived seed reproduces a run perfectly within one boot
    and differs on the next -- §3.6's guarantee, broken where nothing inside a single
    process can see it. M2a shipped exactly that defect once. crc32 is stable across
    processes, platforms and releases.
    """
    return random.Random(
        seed ^ zlib.crc32(":".join(str(part) for part in key).encode())
    )


class NoiseFloor:
    """The background the analysis has to work through, drawn from `seed`.

    `seed` is a parameter rather than read off `settings`, because
    `InspectionClient` already carries a seed of its own that must decide the defect
    draw (`test_constructor_seed_overrides_settings_seed_for_the_defect_draw`), and one
    object that read the settings instead would quietly ignore it.
    """

    def __init__(self, settings: Settings, seed: int) -> None:
        if settings.carrier_count < 1:
            raise ValueError(
                f"carrier_count is {settings.carrier_count!r}: the carrier qualities "
                "are normalised across the pool, and an empty pool has no mean"
            )
        self._s = settings
        self._seed = seed
        # Normalised across the pool this run actually has, so the qualities are a
        # redistribution of the line's scrap and not a shift of it -- see
        # `carrier_quality`. Computed once here because it is a pure function of
        # (seed, carrier_count) and the alternative is eighteen exponentials per part.
        self._quality_scale = 1.0 / statistics.fmean(
            self._raw_quality(carrier) for carrier in range(settings.carrier_count)
        )

    # --- genuine carrier-to-carrier variation, which is not a fault ------------------

    def carrier_quality(self, carrier_id: int) -> float:
        """This carrier's standing multiplier on defect propensity. 1.0 is the pool's
        average carrier.

        Drawn once per run in the sense that matters: a pure function of the seed, the
        carrier id and the size of the pool, so every station and every part agrees
        about carrier 7 without anything being shared.

        Lognormal, not Gaussian: the quantity is a positive scale, and a Gaussian at the
        configured width puts carriers below zero, where a defect propensity is not a
        thing.

        **Scaled so the mean across the pool is exactly 1.** Not so that the
        *distribution* has mean 1, which is a weaker claim and not the one that matters:
        at eighteen carriers the sample mean of a 36 %-spread lognormal is a seed's
        luck, and the shipped seed's happened to be 0.865 -- the line then scraps at
        1.37 % while `reject_rate` says 1.5 %, and §3.5's noise floor has quietly moved
        the number it is a noise floor around. Scaling across the pool makes the
        variation a redistribution, which is what §3.5 asks it to be.

        The cost, stated because it is real: every carrier's quality depends on how many
        carriers there are, so changing `carrier_count` redraws all of them.
        """
        return self._raw_quality(carrier_id) * self._quality_scale

    def _raw_quality(self, carrier_id: int) -> float:
        sigma = self._s.carrier_quality_log_sigma
        return math.exp(
            _stream(self._seed, "carrier-quality", carrier_id).gauss(0.0, sigma)
        )

    # --- baseline scrap, from a distribution unrelated to any injected fault ---------

    def class_propensity(self, carrier_id: int, draws: int) -> float:
        """The baseline probability of one of a part's independent defect draws landing.

        `draws` is how many such draws make up one part, **not** the size of the defect
        vocabulary: `inspection_client` makes one per (lane, class), so it passes
        `len(LANES) * len(DEFECT_CLASSES)` = 12 and not 6. Supplied by the caller rather
        than read here -- `inspection_client` owns the plant's copy of the vocabulary and
        imports `stations.base`, which imports this module, so reading it would be an
        import cycle.

        The per-draw rate is set so that a nominal carrier's chance of carrying at least
        one defect is exactly `reject_rate`: `1 - (1 - r)^(1/draws)`. Dividing by `draws`
        instead lands at 1.4897 % against a configured 1.5 % -- 1.0 part per ten
        thousand, which is arithmetic on the two expressions rather than a measurement,
        and the kind of drift that becomes a corrected comment three milestones later.
        """
        if draws < 1:
            raise ValueError(f"a part needs at least one defect draw, got {draws!r}")
        rate = self._s.reject_rate
        # `-expm1(log1p(-r)/n)` rather than `1 - (1 - r) ** (1 / n)`: the same number,
        # without losing the low-order bits of a 1.5 % rate to the subtraction from 1.
        # `log1p(-1)` is undefined, so a rate of 1 -- which the client tests use to force
        # every part defective -- is answered directly.
        base = 1.0 if rate >= 1.0 else -math.expm1(math.log1p(-rate) / draws)
        return base * self.carrier_quality(carrier_id)

    def scrap_draws(self, part_id: str, count: int) -> tuple[float, ...]:
        """`count` uniforms in [0, 1) for one part, in a fixed order.

        **The stream no fault can reach.** §3.5 asks for baseline scrap "from a
        distribution unrelated to any injected fault", and this is the mechanical form
        of that: a fault moves the *threshold* (`faults.DEFECT_PROPENSITY`) and never
        the draw, so a run with a scenario makes the same draws as a run without one and
        a scenario's signal is never partly its own noise.

        One stream per part rather than one per (part, class): the caller's iteration
        order fixes which draw is which, and at the shipped depth the per-class form
        would build a quarter of a million `random.Random` objects per catch-up to buy
        nothing the fixed order does not already give.
        """
        rng = _stream(self._seed, "baseline-scrap", part_id)
        return tuple(rng.random() for _ in range(count))

    # --- micro-stops -----------------------------------------------------------------

    def micro_stop_seconds(self, station_code: str, cycle: int) -> float:
        """Extra seconds on this cycle's takt, 0.0 on the cycles with no jam.

        §3.5's brief jams. Added to the takt rather than driven through PackML on
        purpose: a micro-stop that put a station into `Held` would need an operator to
        clear it, would be a §3.3 cause candidate, and would pollute every
        cause-candidate query in the project with noise. A jam that the station works
        through by itself is what §3.5 describes, and a longer `TaktTime` is what it
        looks like on the wire.

        The interval in `settings` is line-wide; the per-station rate is it divided by
        the number of stations, so adding a fifth station does not make the line jam
        more often.

        Raises ValueError for an interval so short that a station would jam on every
        cycle, which is a stopped line rather than a noise floor.
        """
        stations = len(self._s.station_takt_seconds)
        takt = self._s.station_takt_seconds[station_code]
        per_cycle = takt / (self._s.micro_stop_mean_interval_seconds * stations)
        if per_cycle >= 1.0:
            raise ValueError(
                f"a {self._s.micro_stop_mean_interval_seconds!r} s mean interval over "
                f"{stations} stations puts a micro-stop on every {station_code} cycle "
                f"({per_cycle:.2f} per cycle): that is a stopped line, not a noise floor"
            )
        rng = _stream(self._seed, "micro-stop", station_code, cycle)
        if rng.random() >= per_cycle:
            return 0.0
        return rng.uniform(
            self._s.micro_stop_min_seconds, self._s.micro_stop_max_seconds
        )

    # --- operator interventions ------------------------------------------------------

    def acknowledge_delay_seconds(self, sequence: int) -> float:
        """How long the operator takes to acknowledge alarm number `sequence`.

        Triangular rather than uniform: most alarms are acknowledged by someone standing
        at the panel and a few wait for a shift change, and a uniform draw would make
        the two equally likely. The three bounds are in `settings` and are chosen as
        plausible, not measured -- nothing in this project has measured a real operator.

        Task 5 is the consumer; it lives here because §3.5 lists operator interventions
        as one of the five things the permanent noise floor is made of, and a delay
        drawn inside the alarm code would be a second RNG stream nothing else could
        reproduce.
        """
        return _stream(self._seed, "ack-delay", sequence).triangular(
            self._s.operator_ack_min_seconds,
            self._s.operator_ack_max_seconds,
            self._s.operator_ack_mode_seconds,
        )

    def is_unnecessary_reset(self, sequence: int) -> bool:
        """§3.5's "occasional unnecessary reset": whether intervention `sequence` was
        one the line did not need. Its own stream, so that the rate can be turned up in
        a test without moving any acknowledge delay."""
        rate = self._s.operator_unnecessary_reset_rate
        return _stream(self._seed, "unnecessary-reset", sequence).random() < rate
