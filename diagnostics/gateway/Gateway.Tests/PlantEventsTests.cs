using Gateway.Opc;
using Opc.Ua;

namespace Gateway.Tests;

/// <summary>
/// §4.1's five event types and §4.2's alarm, written out a third time — the plant states them, the gateway
/// states them, and this file states them again. That is the point: an event notification
/// is a positional EventFieldList, so a field order that quietly moved would re-assign every
/// column with nothing raised, and a test that imported the order it checks would move with
/// it and prove nothing.
/// </summary>
public sealed class PlantEventsTests
{
    private const ushort PlantNamespace = 2;

    private static readonly string[] DefectClasses = ["gap", "crack"];
    private static readonly double[] Confidences = [0.91, 0.04];

    [Fact]
    public void EveryEventTypeCarriesTheFieldsItsStationPublishesInOrder()
    {
        Assert.Equal(
            ["ComponentSerial", "Lane", "LotCode", "Supplier"],
            PlantEvents.ComponentRead.Fields);
        Assert.Equal(
            ["AssemblySerial", "ComponentSerials", "CarrierId"],
            PlantEvents.AssemblyCreated.Fields);
        Assert.Equal(
            ["AssemblySerial", "Curve", "PeakForce", "JoiningDistance"],
            PlantEvents.PartProcessed.Fields);
        Assert.Equal(
            [
                "AssemblySerial", "CarrierId", "Disposition", "DefectClasses", "Confidences",
                "Confidence", "ModelVersion", "Image",
            ],
            PlantEvents.InspectionResult.Fields);
        Assert.Equal(
            ["AssemblySerial", "Disposition", "Reason"],
            PlantEvents.PartCompleted.Fields);
        Assert.Equal(
            [
                "AlarmCode", "AlarmText", "AlarmSeverity", "AlarmRaisedAt", "AlarmActive",
                "AlarmAcknowledged",
            ],
            PlantEvents.Alarm.Fields);
    }

    [Fact]
    public void TheAlarmSharesNoFieldNameWithAnyTypeItRidesBeside()
    {
        // Every station declares the alarm alongside its own type, and two types on one
        // stream may not share a field name -- the plant's historian de-duplicates event
        // columns by node rather than by name, so a collision leaves that station with no
        // event history and no error. The `Alarm` prefix is what buys that, and BaseEventType
        // is the other half: it already carries Severity, Message and Time.
        foreach (var type in PlantEvents.All.Where(type => type != PlantEvents.Alarm))
        {
            // Constructing the stream is the check: EventStreamSpec throws on a collision.
            var stream = new EventStreamSpec([type, PlantEvents.Alarm]);
            Assert.Equal(
                2 + type.Fields.Count + PlantEvents.Alarm.Fields.Count, stream.Fields.Count);
        }
    }

    [Fact]
    public void TheAlarmsInstantAndItsFlagsSurviveTheDecodeAsThemselves()
    {
        // Two shapes no other type carries. A bool falling through to Convert.ToDouble
        // becomes 1 or 0 and the writer can no longer tell "the plant said false" from "the
        // plant said 0.0"; a DateTime throws from inside a decode that names no field.
        // AlarmRaisedAt is §5.2's key for the row, so it has to reach Postgres as an instant.
        var stream = new EventStreamSpec([PlantEvents.Alarm]);
        var raised = new DateTime(2026, 9, 14, 2, 13, 40, DateTimeKind.Utc);
        var acked = new DateTime(2026, 9, 14, 2, 16, 10, DateTimeKind.Utc);

        var record = stream.Decode("S2", "ns=2;i=6",
        [
            new Variant(acked),
            new Variant(new NodeId("AlarmEventType", PlantNamespace)),
            new Variant("A-207"),
            new Variant("joining force out of tolerance"),
            new Variant(700u),
            new Variant(raised),
            new Variant(true),
            new Variant(true),
        ]);

        // The event's own Time is the instant of this transition; AlarmRaisedAt is the
        // alarm's identity, and the two are deliberately different here.
        Assert.Equal(acked, record.SourceTs);
        Assert.Contains("\"AlarmActive\":true", record.PayloadJson, StringComparison.Ordinal);
        Assert.Contains("\"AlarmAcknowledged\":true", record.PayloadJson, StringComparison.Ordinal);
        Assert.Contains("\"AlarmRaisedAt\":\"2026-09-14T02:13:40Z\"", record.PayloadJson, StringComparison.Ordinal);
    }

    [Fact]
    public void AClearedAlarmReportsItselfInactiveRatherThanOmittingTheFlag()
    {
        // `false` is a value and not an absence. Were it dropped the way an empty string is,
        // the clear would be indistinguishable from a raise and the row's cleared_at would
        // never fill -- every alarm active for ever on §3.7's screen.
        var stream = new EventStreamSpec([PlantEvents.Alarm]);

        var record = stream.Decode("S2", "ns=2;i=6",
        [
            new Variant(new DateTime(2026, 9, 14, 2, 16, 13, DateTimeKind.Utc)),
            new Variant(new NodeId("AlarmEventType", PlantNamespace)),
            new Variant("A-207"),
            new Variant("joining force out of tolerance"),
            new Variant(700u),
            new Variant(new DateTime(2026, 9, 14, 2, 13, 40, DateTimeKind.Utc)),
            new Variant(false),
            new Variant(true),
        ]);

        Assert.Contains("\"AlarmActive\":false", record.PayloadJson, StringComparison.Ordinal);
    }

    [Fact]
    public void EveryTypeIsNamedAsThePlantsBrowseNameSpellsIt()
    {
        // The type browse name is what a GeneratesEvent reference resolves to and what the
        // writer routes on. A spelling that drifts from the plant's does not fail to compile;
        // it fails to match, and the stream then ingests into nothing.
        Assert.Equal(
            [
                "ComponentReadEventType", "AssemblyCreatedEventType", "PartProcessedEventType",
                "InspectionResultEventType", "PartCompletedEventType", "AlarmEventType",
            ],
            PlantEvents.All.Select(type => type.TypeName));
    }

    [Fact]
    public void TimeAndTheTypeLeadEverySelectClause()
    {
        // Time is BaseEventType's own and is the event's SourceTimestamp — §5.2's read_at
        // and created_at are that instant, and no type carries a second copy of it. The type
        // reference is what tells two types apart on the one station that emits both.
        var stream = new EventStreamSpec([PlantEvents.ComponentRead, PlantEvents.AssemblyCreated]);

        Assert.Equal(
            [
                "Time", "EventType", "ComponentSerial", "Lane", "LotCode", "Supplier",
                "AssemblySerial", "ComponentSerials", "CarrierId",
            ],
            stream.Fields);
        Assert.Equal(
            stream.Fields,
            stream.BuildFilter().SelectClauses.Select(clause => clause.BrowsePath[0].Name));
    }

    [Fact]
    public void TwoTypesOnOneStreamSharingAFieldNameIsRefused()
    {
        // asyncua historises events per emitting node and de-duplicates the columns by node
        // rather than by name, so two same-named fields on one station produce two identical
        // columns and a CREATE TABLE that fails — swallowed by the historian's own except,
        // leaving that station with no event history and no error. This decoder could not
        // tell the two apart either.
        var clashing = new EventTypeSpec("OtherEventType", ["AssemblySerial"]);

        Assert.Throws<InvalidOperationException>(
            () => new EventStreamSpec([PlantEvents.PartCompleted, clashing]));
    }

    [Fact]
    public void AnEventIsDecodedIntoThePayloadTheWriterRoutesOn()
    {
        // The arrays stay arrays. §5.2 stores the score vector and the curve as arrays, so
        // flattening either here would be the gateway deciding a shape the schema decided.
        var stream = new EventStreamSpec([PlantEvents.InspectionResult]);
        var at = new DateTime(2026, 9, 14, 2, 14, 0, DateTimeKind.Utc);

        var record = stream.Decode("S3", "ns=2;i=9",
        [
            new Variant(at),
            new Variant(new NodeId("InspectionResultEventType", PlantNamespace)),
            new Variant("A-00000001"),
            new Variant(3u),
            new Variant("reject"),
            new Variant(DefectClasses),
            new Variant(Confidences),
            new Variant(0.87),
            new Variant("sim-1"),
            new Variant(new byte[] { 1, 2, 3 }),
        ]);

        Assert.Equal(at, record.SourceTs);
        Assert.Equal([1, 2, 3], record.ImageBytes);
        Assert.Contains("\"DefectClasses\":[\"gap\",\"crack\"]", record.PayloadJson, StringComparison.Ordinal);
        Assert.Contains("\"Confidences\":[0.91,0.04]", record.PayloadJson, StringComparison.Ordinal);
        Assert.Contains("\"EventType\":\"InspectionResultEventType\"", record.PayloadJson, StringComparison.Ordinal);
    }

    [Fact]
    public void AnEmptyFieldIsAbsentRatherThanAValue()
    {
        // The plant sends "" for a field that does not apply, because the field is not
        // optional on the wire. A good part's reason stored as text becomes a disposition
        // with a nameless cause, and an empty ByteString stored as bytes gives every good
        // part a row in inspection_images — absent and empty are different claims.
        var stream = new EventStreamSpec([PlantEvents.PartCompleted]);

        var record = stream.Decode("S4", "ns=2;i=8",
        [
            new Variant(new DateTime(2026, 9, 14, 2, 14, 0, DateTimeKind.Utc)),
            new Variant(new NodeId("PartCompletedEventType", PlantNamespace)),
            new Variant("A-00000002"),
            new Variant("good"),
            new Variant(string.Empty),
        ]);

        Assert.DoesNotContain("Reason", record.PayloadJson, StringComparison.Ordinal);
    }

    [Fact]
    public void AnEventWithNoInstantIsRefusedRatherThanStampedWithTheWallClock()
    {
        // §4.2: SourceTimestamp is simulated time and is what every analysis reads. An event
        // whose own instant did not arrive, stamped "now", lands in the history at a moment
        // that has nothing to do with the part — and nothing downstream can tell it from a
        // real one.
        var stream = new EventStreamSpec([PlantEvents.PartCompleted]);

        Assert.Throws<InvalidOperationException>(() => stream.Decode("S4", "ns=2;i=8",
        [
            Variant.Null,
            new Variant(new NodeId("PartCompletedEventType", PlantNamespace)),
            new Variant("A-00000003"),
            new Variant("good"),
            new Variant(string.Empty),
        ]));
    }

    [Fact]
    public void AStreamOfOneTypeNeedsNoDiscriminator()
    {
        // A station that generates exactly one event type can emit no other, so there is
        // nothing here to tell apart and nothing is being guessed. It is also what keeps the
        // three single-type stations ingesting if the server ever answers the EventType
        // clause with nothing.
        var stream = new EventStreamSpec([PlantEvents.PartCompleted]);

        var record = stream.Decode("S4", "ns=2;i=8",
        [
            new Variant(new DateTime(2026, 9, 14, 2, 14, 0, DateTimeKind.Utc)),
            Variant.Null,
            new Variant("A-00000004"),
            new Variant("good"),
            new Variant(string.Empty),
        ]);

        Assert.Contains("\"EventType\":\"PartCompletedEventType\"", record.PayloadJson, StringComparison.Ordinal);
    }

    [Fact]
    public void AStreamOfTwoTypesRefusesAnEventItCannotAttributeToOne()
    {
        // S1 emits two. Which type a row is cannot be recovered from which columns happen to
        // be non-null — a good part's reason is empty and an inspection event's image is too
        // — so an unattributable event is refused rather than routed by resemblance.
        var stream = new EventStreamSpec(
            [PlantEvents.ComponentRead, PlantEvents.AssemblyCreated]);

        Assert.Throws<InvalidOperationException>(() => stream.Decode("S1", "ns=2;i=5",
        [
            new Variant(new DateTime(2026, 9, 14, 2, 14, 0, DateTimeKind.Utc)),
            Variant.Null,
            new Variant("C-1-00000001"),
        ]));
    }
}
