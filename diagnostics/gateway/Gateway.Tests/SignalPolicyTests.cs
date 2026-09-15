using System.Text.Json;
using Gateway.Opc;
using Opc.Ua;

namespace Gateway.Tests;

public sealed class SignalPolicyTests
{
    private const string MinimalJson = "{}";

    /// <summary>
    /// The four types §4.1 adds in M2b, plus §4.2's alarm. None of them carries an image.
    /// </summary>
    private static readonly string[] ImagelessEventTypes =
    [
        "ComponentReadEventType", "AssemblyCreatedEventType", "PartProcessedEventType",
        "PartCompletedEventType", "AlarmEventType",
    ];

    /// <summary>The three noisy floats §4.1 gives S1, S2 and S4, and nothing else.</summary>
    private static readonly string[] DeadbandedSignals =
    [
        "JoiningDistance", "JoiningForcePeak", "LaneFill_1", "LaneFill_2", "OutfeedFill",
        "TaktTime",
    ];

    /// <summary>
    /// The file the container mounts, not a copy of it. A policy the tests agreed with and the
    /// gateway never saw would prove nothing about the deadbands the plant is actually read
    /// with, and the two would drift the first time one of them was edited.
    /// </summary>
    private static string RealJson =>
        File.ReadAllText(Path.Join(AppContext.BaseDirectory, "config", "signals.json"));

    [Fact]
    public void AnUnknownSignalIsSubscribedWithNoDeadband()
    {
        // The case that matters. Topology is discovered, so this will happen -- and a
        // policy that failed closed would lose a whole stream silently, which is the
        // exact failure shape M1 spent four risks measuring.
        var policy = SignalPolicy.Parse(MinimalJson);
        var rule = policy.For("SomeSignalNobodyPlanned", BuiltInType.Double);
        Assert.True(rule.Subscribe);
        Assert.Null(rule.Deadband);
    }

    [Fact]
    public void CountersNeverGetADeadband()
    {
        // §5.1's lesson from M1: a deadband on a monotonic counter silently loses parts.
        var policy = SignalPolicy.Parse(RealJson);
        foreach (var counter in new[] { "PartCount", "GoodCount", "RejectCount" })
        {
            Assert.Null(policy.For(counter, BuiltInType.UInt32).Deadband);
        }
    }

    [Fact]
    public void BufferLevelGetsNoDeadbandEitherBecauseItMovesByOne()
    {
        // A deadband of 0.5 on a level that steps by 1 would swallow half the
        // transitions, and Task 12's propagation proof watches B2_3 drain one at a time.
        Assert.Null(SignalPolicy.Parse(RealJson).For("Level", BuiltInType.UInt32).Deadband);
    }

    [Fact]
    public void StateAndItsReasonAreNeverDeadbanded()
    {
        // A deadband on a string is meaningless, and every transition matters.
        var policy = SignalPolicy.Parse(RealJson);
        Assert.Null(policy.For("State", BuiltInType.String).Deadband);
        Assert.Null(policy.For("StateReason", BuiltInType.String).Deadband);
    }

    [Fact]
    public void NoisyFloatsKeepTheirConfiguredDeadband()
    {
        Assert.Equal(0.05, SignalPolicy.Parse(RealJson).For("TaktTime", BuiltInType.Double).Deadband);
    }

    [Fact]
    public void AMalformedPolicyFileFailsLoudlyRatherThanSilentlyDefaulting()
    {
        // Falling back to "no policy" here would be indistinguishable from a policy that
        // happened to name nothing, and the operator would never learn the file is wrong.
        Assert.Throws<JsonException>(() => SignalPolicy.Parse("{ not json"));
    }

    [Fact]
    public void AMisspeltKeyIsRefusedRatherThanIgnored()
    {
        // The one place this file does not fail open. "deadbnd" read as "no deadband" is
        // indistinguishable from a signal meant to have none, so the tuning an engineer
        // measured would silently stop applying and nothing would say so. The signal set is
        // open and discovered; the key set is closed and enumerated.
        Assert.Throws<JsonException>(() => SignalPolicy.Parse(
            """{ "signals": { "TaktTime": { "deadbnd": 0.05 } } }"""));
    }

    [Fact]
    public void ANumberThatCannotBeADeadbandOrAPageIsRefused()
    {
        Assert.Throws<JsonException>(() => SignalPolicy.Parse(
            """{ "signals": { "TaktTime": { "deadband": -1 } } }"""));
        Assert.Throws<JsonException>(() => SignalPolicy.Parse(
            """{ "signals": { "TaktTime": { "page_size": 0 } } }"""));
        Assert.Throws<JsonException>(() => SignalPolicy.Parse(
            """{ "defaults": { "page_size": -5 } }"""));
    }

    [Fact]
    public void ADeadbandOnANonNumericSignalIsDroppedRatherThanSentToTheServer()
    {
        // The plant decides types, the policy names signals, and the pair can disagree. An
        // absolute deadband on a String comes back BadDeadbandFilterInvalid and leaves that
        // item uncreated -- a lost stream, which is the one outcome §5.1 refuses. Extra rows
        // are the safe direction.
        var policy = SignalPolicy.Parse(
            """{ "signals": { "State": { "deadband": 0.5 } } }""");
        Assert.Null(policy.For("State", BuiltInType.String).Deadband);
        Assert.True(policy.For("State", BuiltInType.String).Subscribe);
    }

    [Fact]
    public void OnlyTheSignalNamedWithSubscribeFalseLosesItsSubscription()
    {
        // The escape hatch that makes fail-open safe to adopt: meeting an unwanted stream on a
        // real plant is answered by naming it, not by changing the default for every signal
        // nobody has named yet.
        //
        // This is the rule, not the behaviour. That the rule is honoured on *both* write paths
        // -- which for three revisions it was not, the backfill storing every row of a stream
        // the subscription had dropped -- is HistoryBackfillTests'
        // APolicyCanKeepADiscoveredStreamOutOfStorage, which asserts nothing is stored.
        var policy = SignalPolicy.Parse(
            """{ "signals": { "Noise": { "subscribe": false } } }""");
        Assert.False(policy.For("Noise", BuiltInType.Double).Subscribe);
        Assert.True(policy.For("TaktTime", BuiltInType.Double).Subscribe);
    }

    [Fact]
    public void PageSizeFallsBackToTheDefaultAndTheShippedFileStatesIt()
    {
        // The backfill reads every variable stream at SignalRule.PageSize and nowhere else, so
        // a signal the file names and one it has never heard of must be paged the same way --
        // otherwise "an unknown signal is subscribed" would still lose it to a page size
        // nobody stated.
        var policy = SignalPolicy.Parse(RealJson);
        Assert.Equal(
            SignalPolicy.DefaultPageSize,
            policy.For("TaktTime", BuiltInType.Double).PageSize);
        Assert.Equal(
            SignalPolicy.DefaultPageSize,
            policy.For("NobodyPlannedThisEither", BuiltInType.Double).PageSize);
    }

    [Fact]
    public void APerSignalPageSizeWins()
    {
        var policy = SignalPolicy.Parse(
            """{ "defaults": { "page_size": 1000 }, "signals": { "Image": { "page_size": 25 } } }""");
        Assert.Equal(25, policy.For("Image", BuiltInType.ByteString).PageSize);
    }

    [Fact]
    public void APolicyThatIsNotMountedIsAStartUpFailure()
    {
        // Not a silent default: an operator who mounted the file to the wrong path has to
        // find out at boot rather than from deadbands that quietly stopped applying.
        var missing = Path.Join(Path.GetTempPath(), "no-such-policy-" + Path.GetRandomFileName());
        Assert.Throws<FileNotFoundException>(() => SignalPolicy.Load(missing));
    }

    [Fact]
    public void TheShippedPolicyPagesEveryEventTypeTheLineHas()
    {
        // D4: the page size is per event type because what one page weighs is what its events
        // carry. Only §3.4's verdict carries image bytes, and it is the only one that still
        // has to be read 25 at a time -- at 25 the other four would cost ~2,500 round trips
        // over a 33 h history on S1 and ~800 on S2 and S4, where 33 do.
        var policy = SignalPolicy.Parse(RealJson);

        Assert.Equal(25, policy.ForEvent("InspectionResultEventType").PageSize);
        Assert.All(
            ImagelessEventTypes,
            type => Assert.True(
                policy.ForEvent(type).PageSize >= 2_000,
                $"{type} carries no images and is paged as if it did"));
    }

    [Fact]
    public void EveryEventPageStaysUnderTheCeilingWhereTruncationStopsBeingDetectable()
    {
        // A read returning HistoryBackfill.SilentTruncationCeiling values cannot be told from
        // one the server cut off there, and ClassifyPage cannot see it because the page is not
        // full to what was asked for. A page size at or above the ceiling therefore fails
        // every window it is used on -- which is loud, but it is a boot-time loud that belongs
        // here instead.
        var policy = SignalPolicy.Parse(RealJson);

        Assert.All(
            policy.KnownEventTypes,
            type => Assert.True(
                policy.ForEvent(type).PageSize < HistoryBackfill.SilentTruncationCeiling,
                $"{type} is paged at or above the silent-truncation ceiling"));
    }

    [Fact]
    public void TheShippedPolicyNamesEveryTypeThisGatewayCanDecode()
    {
        // The fail-open default means an unnamed type is still read, so nothing breaks when
        // one is missing -- it is read at a number nobody measured, which is exactly the kind
        // of silence D4's per-type page sizes exist to replace. §4.2's alarm was the sixth
        // type and had no entry when it was first added; this is what says so at boot.
        var policy = SignalPolicy.Parse(RealJson);

        Assert.Equal(
            PlantEvents.All.Select(type => type.TypeName).OrderBy(n => n, StringComparer.Ordinal),
            policy.KnownEventTypes.OrderBy(n => n, StringComparer.Ordinal));
    }

    [Fact]
    public void AnEventTypeNobodyNamedIsPagedRatherThanSkipped()
    {
        // The event half of the fail-open rule. Event types are discovered from the plant's
        // own GeneratesEvent references, so this file will meet ones it does not name.
        Assert.Equal(
            SignalPolicy.DefaultEventPageSize,
            SignalPolicy.Parse(MinimalJson).ForEvent("SomeTypeNobodyPlanned").PageSize);
    }

    [Fact]
    public void AnEventEntryCarryingASignalsKeyIsRefused()
    {
        // The key set stays closed on this side too. `subscribe` and `deadband` mean nothing
        // for an event stream -- §3.4's history has no off switch and an event has no
        // magnitude to compare -- so one written here is a misunderstanding an operator has
        // to learn about at boot rather than a setting that silently does nothing.
        Assert.Throws<JsonException>(() => SignalPolicy.Parse(
            """{ "events": { "PartCompletedEventType": { "subscribe": false } } }"""));
        Assert.Throws<JsonException>(() => SignalPolicy.Parse(
            """{ "events": { "PartCompletedEventType": { "page_size": 0 } } }"""));
    }

    [Fact]
    public void TheShippedPolicyNamesEveryDeadbandTheLineNeedsAndNoOthers()
    {
        // A deadband that appeared on a seventh signal would be a silent filter on a stream
        // nobody chose to filter, which no other test here would notice.
        var policy = SignalPolicy.Parse(RealJson);
        var deadbanded = policy.KnownSignals
            .Where(signal => policy.For(signal, BuiltInType.Double).Deadband is not null)
            .OrderBy(signal => signal, StringComparer.Ordinal)
            .ToList();

        Assert.Equal(DeadbandedSignals, deadbanded);
    }
}
